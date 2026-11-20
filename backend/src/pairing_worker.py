import logging
import os
from pathlib import Path
from redis import Redis
from sqlalchemy import select
from src.config.database import Session
from src.models.pairing import PairingRun
from src.models import user, study, observation, run, review, audit, pairing  # noqa: F401
from src.services.pairing_runs import calculate_run

redis = Redis.from_url(os.environ['REDIS_URL'])
Path('/app/artifacts/pairing').mkdir(parents=True, exist_ok=True)


def recover():
    """重启后把未完成的配对任务重新排队（本应用只有一个 worker）。"""
    with Session() as db:
        pending = list(db.scalars(
            select(PairingRun.id).where(PairingRun.status.in_(['queued', 'running']))))
    for run_id in pending:
        redis.rpush('pairing-queue', run_id)


if os.environ.get('PAIRING_WORKER_RECOVER', 'true') == 'true':
    recover()

while True:
    Path('/tmp/worker-heartbeat').touch()
    job = redis.blpop('pairing-queue', timeout=2)
    if not job:
        continue
    run_id = int(job[1])
    with Session() as db:
        try:
            calculate_run(db, run_id)
        except Exception:  # pragma: no cover - 防御性日志
            logging.exception('pairing run %s failed', run_id)
