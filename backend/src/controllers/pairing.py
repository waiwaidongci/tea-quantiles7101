from src.services import pairing_runs
from src.schemas.pairing import PairingRunInput


# 控制器把接口约定与持久化/算法分开，便于独立测试服务层。
def overview(db, study_id=None):
    return {'studies': pairing_runs.list_studies(db), 'runs': pairing_runs.list_runs(db, study_id)}


def create_study(db, data, user):
    return pairing_runs.create_study(db, data, user)


def list_readings(db, study_id):
    return pairing_runs.readings(db, study_id)


def correct(db, study_id, data, user):
    return pairing_runs.apply_correction(db, study_id, data, user)


def create_run(db, study_id, data, user):
    return pairing_runs.create_run(db, study_id, data, user)


def get_run(db, run_id):
    return pairing_runs.get_run(db, run_id)


def review_run(db, run_id, data, user):
    return pairing_runs.approve(db, run_id, data, user)
