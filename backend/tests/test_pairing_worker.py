"""worker 侧端到端：队列取任务 → 只读冻结快照 → 写结果 → 重复消费幂等。

用 SQLite 与内存队列替身，覆盖 ``calculate_run`` 的完整路径与失败态。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.config.database import Base
from src.models.pairing import PairingReading, PairingRun, PairingStudy
from src.services import pairing_runs
from src.utils.serializers import checksum


class _User:
    email = 'analyst@demo.local'


@pytest.fixture()
def sessions():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _seed(sessions):
    with sessions() as db:
        study = PairingStudy(name='端到端', revision=0, created_at='t')
        db.add(study)
        db.flush()
        for booklet, key, minute, moisture in [
            ('initial', 'I0', 15, '2.2'), ('initial', 'I1', 10, '0.4'),
            ('retest', 'R0', 6, '0.2'), ('retest', 'R1', 13, '2.9'), ('retest', 'R2', 6, '2.0'),
        ]:
            db.add(PairingReading(pairing_study_id=study.id, booklet=booklet,
                                  source_key=key, sampled_at=minute, moisture=moisture,
                                  updated_at='t'))
        db.commit()
        return study.id


def _run(sessions, study_id, tolerance=8):
    from src.schemas.pairing import PairingRunInput
    pairing_runs.enqueue = lambda run_id: None
    with sessions() as db:
        return pairing_runs.create_run(
            db, study_id, PairingRunInput(name='端到端运行', tolerance_minutes=tolerance), _User())


def test_result_json_shape_matches_page(sessions):
    study_id = _seed(sessions)
    run = _run(sessions, study_id)
    with sessions() as db:
        pairing_runs.calculate_run(db, run['id'])
    with sessions() as db:
        data = pairing_runs.get_run(db, run['id'])
    result = data['result']
    assert result['input_sha256'] == data['checksum']
    for key in ('tolerance_minutes', 'cardinality', 'total_time_diff',
                'bound_lower', 'bound_upper', 'verified', 'checks'):
        assert key in result
    for bound in ('bound_lower', 'bound_upper'):
        assert set(result[bound]) == {'twice_median_micro', 'median', 'pairing'}
        assert isinstance(result[bound]['twice_median_micro'], int)
        for row in result[bound]['pairing']:
            assert set(row) == {'initial_key', 'initial_time', 'retest_key',
                                'retest_time', 'time_diff', 'delta'}
    # 页面按明细复算两倍中位数，必须与后端一致
    for bound in ('bound_lower', 'bound_upper'):
        pairing_rows = result[bound]['pairing']
        deltas = sorted(round(float(row['delta']) * 1_000_000) for row in pairing_rows)
        k = len(deltas)
        twice = 2 * deltas[k // 2] if k % 2 else deltas[k // 2 - 1] + deltas[k // 2]
        assert twice == result[bound]['twice_median_micro']


def test_calculate_run_is_idempotent(sessions):
    study_id = _seed(sessions)
    run = _run(sessions, study_id)
    with sessions() as db:
        pairing_runs.calculate_run(db, run['id'])
    with sessions() as db:
        first = pairing_runs.get_run(db, run['id'])
    with sessions() as db:
        pairing_runs.calculate_run(db, run['id'])
    with sessions() as db:
        second = pairing_runs.get_run(db, run['id'])
    assert first['result'] == second['result']
    assert json.dumps(first['result'], sort_keys=True) == json.dumps(second['result'], sort_keys=True)


def test_checksum_tamper_fails_run(sessions):
    study_id = _seed(sessions)
    run = _run(sessions, study_id)
    with sessions() as db:
        stored = db.get(PairingRun, run['id'])
        stored.snapshot = {'initial': [], 'retest': []}   # 篡改快照
        db.commit()
    with sessions() as db:
        pairing_runs.calculate_run(db, run['id'])
    with sessions() as db:
        data = pairing_runs.get_run(db, run['id'])
    assert data['status'] == 'failed'
    assert '快照校验失败' in data['error']
