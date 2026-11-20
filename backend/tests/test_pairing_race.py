"""冻结与更正的并发语义（真实 SQLAlchemy 会话 + 事务）。

场景：worker 准备冻结两册时，另一连接同时提交“两册一起更正”的事务。
要求冻结的初测与复测同属更正之前或更正之后，绝不能出现一册新一册旧；
重跑所得结果与配对明细逐字节一致。
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from src.config.database import Base
from src.models.pairing import PairingReading, PairingRun, PairingStudy
from src.services import pairing_runs
from src.utils.serializers import checksum


class _User:
    email = 'analyst@demo.local'


@pytest.fixture()
def engine(tmp_path):
    path = tmp_path / 'race.db'
    eng = create_engine(
        f'sqlite:///{path}',
        connect_args={'check_same_thread': False, 'timeout': 30},
        poolclass=None,
    )

    @event.listens_for(eng, 'connect')
    def _pragmas(conn, _record):
        cur = conn.cursor()
        cur.execute('PRAGMA journal_mode=WAL')
        cur.execute('PRAGMA busy_timeout=30000')
        cur.close()

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _sessions(eng):
    return sessionmaker(bind=eng, expire_on_commit=False)


def _seed(sessions, initial, retest):
    with sessions() as db:
        study = PairingStudy(name='并发研究', revision=0, created_at='2026-11-20T00:00:00+00:00')
        db.add(study)
        db.flush()
        for booklet, rows in (('initial', initial), ('retest', retest)):
            for key, minute, moisture in rows:
                db.add(PairingReading(
                    pairing_study_id=study.id, booklet=booklet, source_key=key,
                    sampled_at=minute, moisture=moisture, updated_at='seed'))
        db.commit()
        return study.id


def _readings(db, study_id):
    rows = db.scalars(select(PairingReading).where(
        PairingReading.pairing_study_id == study_id)).all()
    return {
        'initial': sorted((r.source_key, r.sampled_at, r.moisture) for r in rows if r.booklet == 'initial'),
        'retest': sorted((r.source_key, r.sampled_at, r.moisture) for r in rows if r.booklet == 'retest'),
    }


def test_correction_is_atomic_across_both_booklets(engine):
    sessions = _sessions(engine)
    study_id = _seed(sessions,
                     [('I1', 0, '1.0'), ('I2', 10, '2.0')],
                     [('R1', 0, '1.1'), ('R2', 10, '2.2')])

    start = threading.Barrier(2)
    snapshots = []
    errors = []

    def freezer():
        with sessions() as db:
            start.wait()
            for _ in range(6):
                snapshot, revision = pairing_runs.freeze_snapshot(db, study_id)
                snapshots.append((revision, snapshot))

    def corrector():
        with sessions() as db:
            start.wait()
            from src.schemas.pairing import PairingCorrectionInput, PairingReadingInput
            data = PairingCorrectionInput(readings=[
                PairingReadingInput(booklet='initial', source_key='I1', sampled_at=0, moisture='9.0'),
                PairingReadingInput(booklet='retest', source_key='R2', sampled_at=10, moisture='9.9'),
            ])
            for _ in range(3):
                pairing_runs.apply_correction(db, study_id, data, _User())

    threads = [threading.Thread(target=freezer), threading.Thread(target=corrector)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), '并发测试超时'
    assert len(snapshots) == 6

    with sessions() as db:
        final = _readings(db, study_id)
    old = {'initial': [('I1', 0, '1.0'), ('I2', 10, '2.0')],
           'retest': [('R1', 0, '1.1'), ('R2', 10, '2.2')]}
    # 更正写入的 '9.0' 会被规范成 '9'（十进制定点、去尾零）
    new = {'initial': [('I1', 0, '9'), ('I2', 10, '2.0')],
           'retest': [('R1', 0, '1.1'), ('R2', 10, '9.9')]}
    for revision, snapshot in snapshots:
        frozen = {
            'initial': sorted((row['source_key'], row['sampled_at'], row['moisture'])
                              for row in snapshot['initial']),
            'retest': sorted((row['source_key'], row['sampled_at'], row['moisture'])
                             for row in snapshot['retest']),
        }
        if frozen == old:
            assert revision == 0
        else:
            # 只要含一处更正，就必须两册都属于“更正之后”的状态
            assert frozen == new, f'撕裂快照：{frozen}'
            assert revision >= 1
    assert final in (old, new)


def test_frozen_run_is_immutable_after_correction(engine):
    sessions = _sessions(engine)
    study_id = _seed(sessions,
                     [('I1', 0, '1.0'), ('I2', 10, '2.0')],
                     [('R1', 0, '1.1'), ('R2', 10, '2.4')])
    from src.schemas.pairing import (
        PairingCorrectionInput, PairingReadingInput, PairingRunInput,
    )

    enqueued = []
    pairing_runs.enqueue = lambda run_id: enqueued.append(run_id)
    with sessions() as db:
        run = pairing_runs.create_run(db, study_id, PairingRunInput(name='冻结一', tolerance_minutes=5), _User())
    frozen_checksum = run['checksum']
    frozen_snapshot = run['snapshot']

    with sessions() as db:
        pairing_runs.apply_correction(db, study_id, PairingCorrectionInput(readings=[
            PairingReadingInput(booklet='initial', source_key='I1', sampled_at=0, moisture='8.0'),
        ]), _User())

    with sessions() as db:
        stored = db.get(PairingRun, run['id'])
        assert stored.snapshot == frozen_snapshot
        assert stored.checksum == frozen_checksum
        assert checksum(stored.snapshot) == frozen_checksum
        pairing_runs.calculate_run(db, run['id'])
    with sessions() as db:
        first = pairing_runs.get_run(db, run['id'])
        pairing_runs.calculate_run(db, run['id'])
    with sessions() as db:
        second = pairing_runs.get_run(db, run['id'])
    assert first['status'] == 'completed'
    assert first['result'] == second['result']


def test_no_pairing_run_completes_with_reason(engine):
    sessions = _sessions(engine)
    study_id = _seed(sessions,
                     [('I1', 0, '1.0')],
                     [('R1', 1000, '1.2')])
    from src.schemas.pairing import PairingRunInput
    pairing_runs.enqueue = lambda run_id: None
    with sessions() as db:
        run = pairing_runs.create_run(db, study_id, PairingRunInput(name='无配对', tolerance_minutes=30), _User())
        pairing_runs.calculate_run(db, run['id'])
        data = pairing_runs.get_run(db, run['id'])
    assert data['status'] == 'completed'
    assert data['result']['cardinality'] == 0
    assert data['result']['reason']


def test_review_cannot_be_overwritten(engine):
    sessions = _sessions(engine)
    study_id = _seed(sessions,
                     [('I1', 0, '1.0')],
                     [('R1', 0, '1.2')])
    from fastapi import HTTPException
    from src.schemas.pairing import PairingReviewInput, PairingRunInput

    pairing_runs.enqueue = lambda run_id: None
    with sessions() as db:
        run = pairing_runs.create_run(db, study_id, PairingRunInput(name='复核', tolerance_minutes=5), _User())
        pairing_runs.calculate_run(db, run['id'])
        pairing_runs.approve(db, run['id'], PairingReviewInput(note='首次复核意见'), _User())
        with pytest.raises(HTTPException) as exc:
            pairing_runs.approve(db, run['id'], PairingReviewInput(note='试图覆盖第一次复核意见'), _User())
    assert exc.value.status_code == 409


def test_torn_read_is_detected_and_retried(engine):
    """强制在两册读取之间插入一次完整更正，验证 revision 变化会让本次快照作废。

    第一轮读到“初测已改、复测未改”的撕裂状态；revision 检查发现变化，
    freeze_snapshot 丢弃它并重试，最终冻结的两册都属于更正之后。
    """
    sessions = _sessions(engine)
    study_id = _seed(sessions,
                     [('I1', 0, '1.0')],
                     [('R1', 0, '1.1')])
    from src.schemas.pairing import PairingCorrectionInput, PairingReadingInput

    state = {'fired': False}

    def hook(kind, sid):
        if kind != 'between-booklets' or state['fired']:
            return
        state['fired'] = True
        with sessions() as other:
            pairing_runs.apply_correction(other, sid, PairingCorrectionInput(readings=[
                PairingReadingInput(booklet='initial', source_key='I1', sampled_at=0, moisture='7.0'),
                PairingReadingInput(booklet='retest', source_key='R1', sampled_at=0, moisture='7.7'),
            ]), _User())

    pairing_runs.FREEZE_HOOK = hook
    try:
        with sessions() as db:
            snapshot, revision = pairing_runs.freeze_snapshot(db, study_id)
    finally:
        pairing_runs.FREEZE_HOOK = None

    assert state['fired'] is True
    assert revision >= 1
    assert [(row['source_key'], row['moisture']) for row in snapshot['initial']] == [('I1', '7')]
    assert [(row['source_key'], row['moisture']) for row in snapshot['retest']] == [('R1', '7.7')]
