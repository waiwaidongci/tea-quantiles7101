"""两册配对研究的业务服务。

冻结与更正的并发规则
--------------------
* 一次“更正”可以在同一请求里同时改初测册和复测册（``apply_correction``），
  在**同一数据库事务**内提交，并让 ``PairingStudy.revision`` 加一。
* ``freeze_snapshot`` 用“读 revision → 读两册 → 读 revision”的模式取快照：
  两次 revision 相同才接受，否则整体重试。因此冻结的初测与复测一定同属
  更正之前或更正之后，不会混入两次状态。
* 运行创建时把快照与 revision 一起写入 ``PairingRun``；worker 只读数据库里
  的快照，之后的更正不会改写历史冻结的输入或结果。
"""

from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.models.pairing import PairingReading, PairingReview, PairingRun, PairingStudy
from src.services import pairing
from src.services.audits import log
from src.utils.serializers import checksum, now, record

BOOKLETS = ('initial', 'retest')
FREEZE_RETRIES = 8


def _bad(message):
    return HTTPException(422, message)


def _moisture_text(value):
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise _bad('含水率必须是数值')
    if not dec.is_finite() or dec < 0 or dec > 100:
        raise _bad('含水率必须是 0 到 100 之间的有限数值')
    if dec != dec.quantize(Decimal('0.000001')):
        raise _bad('含水率最多 6 位小数')
    return format(dec.normalize(), 'f')


def list_studies(db):
    data = []
    for study in db.scalars(select(PairingStudy).order_by(PairingStudy.id)):
        rows = list(db.scalars(select(PairingReading).where(PairingReading.pairing_study_id == study.id)))
        data.append({
            **record(study),
            'initial_count': sum(1 for row in rows if row.booklet == 'initial'),
            'retest_count': sum(1 for row in rows if row.booklet == 'retest'),
        })
    return data


def _require(db, study_id):
    study = db.get(PairingStudy, study_id)
    if not study:
        raise HTTPException(404, '配对研究不存在')
    return study


def create_study(db, data, user):
    if db.scalar(select(PairingStudy).where(PairingStudy.name == data.name)):
        raise HTTPException(409, '配对研究名称已存在')
    study = PairingStudy(name=data.name, description=data.description, revision=0, created_at=now())
    db.add(study)
    log(db, user, 'pairing.study.create', data.name)
    db.commit()
    return record(study)


def readings(db, study_id):
    _require(db, study_id)
    rows = db.scalars(
        select(PairingReading)
        .where(PairingReading.pairing_study_id == study_id)
        .order_by(PairingReading.booklet, PairingReading.sampled_at, PairingReading.source_key)
    )
    return [record(row) for row in rows]


def apply_correction(db, study_id, data, user):
    """一次更正：同事务改两册并 revision+1。"""
    study = _require(db, study_id)
    normalized = []
    seen = set()
    for item in data.readings:
        if item.booklet not in BOOKLETS:
            raise _bad('册别必须是 initial 或 retest')
        key = (item.booklet, item.source_key)
        if key in seen:
            raise _bad(f'{item.source_key} 在同一册内重复出现')
        seen.add(key)
        if isinstance(item.sampled_at, bool) or item.sampled_at != int(item.sampled_at):
            raise _bad('采样时间必须是整数分钟')
        if not 0 <= item.sampled_at <= pairing.MAX_SAMPLE_MINUTE:
            raise _bad(f'采样时间必须在 0–{pairing.MAX_SAMPLE_MINUTE} 分钟之间')
        normalized.append((item.booklet, item.source_key, int(item.sampled_at),
                           _moisture_text(item.moisture)))

    # 行锁：Postgres 下 SELECT ... FOR UPDATE 串行化同一研究的并发更正。
    # 取得锁后刷新，避免长会话里读到过期的 revision。
    db.execute(select(PairingStudy.id).where(PairingStudy.id == study_id).with_for_update())
    db.refresh(study)
    busy = False
    for booklet, source_key, sampled_at, moisture in normalized:
        row = db.scalar(select(PairingReading).where(
            PairingReading.pairing_study_id == study_id,
            PairingReading.booklet == booklet,
            PairingReading.source_key == source_key))
        if row:
            row.sampled_at = sampled_at
            row.moisture = moisture
            row.updated_at = now()
        else:
            count = len(list(db.scalars(select(PairingReading.id).where(
                PairingReading.pairing_study_id == study_id,
                PairingReading.booklet == booklet))))
            if count >= pairing.MAX_READINGS_PER_BOOKLET:
                raise _bad(f'{booklet} 册已到 {pairing.MAX_READINGS_PER_BOOKLET} 份上限')
            db.add(PairingReading(pairing_study_id=study_id, booklet=booklet,
                                  source_key=source_key, sampled_at=sampled_at,
                                  moisture=moisture, updated_at=now()))
    study.revision = (study.revision or 0) + 1
    log(db, user, 'pairing.correction', f'{study_id}: {len(normalized)} 行，revision {study.revision}')
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '同册源记录编号冲突，请重试')
    db.refresh(study)
    return {'revision': study.revision, 'applied': len(normalized)}


def freeze_snapshot(db, study_id):
    """读 revision → 读两册 → 再读 revision；两次一致才接受。

    每次尝试前 ``db.expire_all()``：SQLAlchemy 的身份映射会缓存已加载对象，
    若不清掉，重试时会读到上一轮的旧读数/旧版本号，看似一致却其实是撕裂
    快照。Postgres 下每条 SELECT 都读已提交的最新行。
    """
    hook = globals().get('FREEZE_HOOK')
    for _ in range(FREEZE_RETRIES):
        db.expire_all()
        study = _require(db, study_id)
        before = study.revision
        initial = [{'source_key': r.source_key, 'sampled_at': r.sampled_at, 'moisture': r.moisture}
                   for r in db.scalars(select(PairingReading).where(
                       PairingReading.pairing_study_id == study_id,
                       PairingReading.booklet == 'initial').order_by(PairingReading.id))]
        if hook:
            hook('between-booklets', study_id)
        retest = [{'source_key': r.source_key, 'sampled_at': r.sampled_at, 'moisture': r.moisture}
                  for r in db.scalars(select(PairingReading).where(
                      PairingReading.pairing_study_id == study_id,
                      PairingReading.booklet == 'retest').order_by(PairingReading.id))]
        db.expire_all()
        after = db.get(PairingStudy, study_id).revision
        if before == after:
            return {'initial': initial, 'retest': retest}, before
    raise HTTPException(409, '两册正在被并发更正，请稍后重试')


def create_run(db, study_id, data, user):
    _require(db, study_id)
    if isinstance(data.tolerance_minutes, bool) or not (
            1 <= data.tolerance_minutes <= pairing.MAX_TOLERANCE_MINUTES):
        raise _bad(f'允许时差必须在 1–{pairing.MAX_TOLERANCE_MINUTES} 分钟之间')
    snapshot, revision = freeze_snapshot(db, study_id)
    # 冻结时就地校验一次，输入不合法立即拒绝，不占用队列。
    try:
        pairing.prepare(snapshot, data.tolerance_minutes)
    except pairing.PairingError as exc:
        raise _bad(str(exc))
    run = PairingRun(
        pairing_study_id=study_id,
        name=data.name,
        status='queued',
        parameters={'tolerance_minutes': data.tolerance_minutes},
        snapshot=snapshot,
        checksum=checksum(snapshot),
        frozen_revision=revision,
        created_at=now(),
    )
    db.add(run)
    db.flush()
    log(db, user, 'pairing.run.queue', f'{run.id}: revision {revision}')
    db.commit()
    enqueue(run.id)
    return record(run)


def enqueue(run_id):
    """把任务推入 Redis；延迟导入，便于无 Redis 的单元测试替换。"""
    import os
    from redis import Redis
    Redis.from_url(os.environ['REDIS_URL']).rpush('pairing-queue', run_id)


def get_run(db, run_id):
    run = db.get(PairingRun, run_id)
    if not run:
        raise HTTPException(404, '配对运行不存在')
    data = record(run)
    review = db.scalar(select(PairingReview).where(PairingReview.run_id == run_id))
    data['review'] = record(review) if review else None
    return data


def list_runs(db, study_id=None):
    query = select(PairingRun.id).order_by(PairingRun.id.desc())
    if study_id is not None:
        query = query.where(PairingRun.pairing_study_id == study_id)
    return [get_run(db, run_id) for run_id in db.scalars(query)]


def approve(db, run_id, data, user):
    run = db.get(PairingRun, run_id)
    if not run:
        raise HTTPException(404, '配对运行不存在')
    if run.status != 'completed':
        raise HTTPException(409, '只能复核已完成的运行')
    if db.scalar(select(PairingReview).where(PairingReview.run_id == run_id)):
        raise HTTPException(409, '已复核，记录不可覆盖')
    obj = PairingReview(run_id=run_id, note=data.note, author=user.email, created_at=now())
    db.add(obj)
    log(db, user, 'pairing.review.approve', f'{run_id}: {data.note}')
    db.commit()
    return record(obj)


def calculate_run(db, run_id):
    """worker 调用：只读冻结快照，计算并写回结果。"""
    run = db.get(PairingRun, run_id)
    if not run or run.status in ('completed', 'failed'):
        return
    run.status = 'running'
    db.commit()
    try:
        if checksum(run.snapshot) != run.checksum:
            raise pairing.PairingError('输入快照校验失败')
        result = pairing.analyze(run.snapshot, run.parameters['tolerance_minutes'])
        if not result.get('verified'):
            raise pairing.PairingError('结果未通过核对，拒绝发布')
        result['input_sha256'] = run.checksum
        run.result = result
        run.status = 'completed'
        run.error = ''
    except pairing.PairingError as exc:
        run.status = 'failed'
        run.error = str(exc)
    db.commit()
