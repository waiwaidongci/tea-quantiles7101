"""HTTP 层端到端：登录 → 建研究 → 更正读数 → 发起研究 → worker 计算 → 复核。

用 FastAPI TestClient + SQLite，覆盖路由、权限、冻结校验、响应里的核对字段。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.config.database import Base, get_db
from src.main import app
from src.models.user import User
from src.middlewares import rate_limit
from src.services import pairing_runs
from src.utils.security import hash_password


class _FakeRedis:
    """限流中间件只需要 incr/expire；测试里不连真实 Redis。"""

    def __init__(self):
        self.counters = {}

    def incr(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def expire(self, key, seconds):
        return True


@pytest.fixture()
def client(monkeypatch):
    engine = create_engine(
        'sqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as db:
        for role in ('admin', 'analyst', 'viewer'):
            db.add(User(email=f'{role}@demo.local', role=role, password_hash=hash_password('Research2026!')))
        db.commit()

    def override():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(pairing_runs, 'enqueue', lambda run_id: None)
    monkeypatch.setattr(rate_limit, 'redis', _FakeRedis())
    with TestClient(app) as test_client:
        yield test_client, Session
    app.dependency_overrides.clear()


def _token(client, role):
    response = client.post('/auth/login', json={'email': f'{role}@demo.local', 'password': 'Research2026!'})
    assert response.status_code == 200
    return {'Authorization': 'Bearer ' + response.json()['token']}


def _create_study(client, headers, name='杯签配对'):
    response = client.post('/pairing/studies', headers=headers, json={'name': name, 'description': ''})
    assert response.status_code == 200, response.text
    return response.json()['id']


def _correct(client, headers, study_id, rows):
    response = client.post(f'/pairing/studies/{study_id}/corrections', headers=headers, json={'readings': rows})
    assert response.status_code == 200, response.text
    return response.json()


def test_viewer_cannot_correct_or_launch(client):
    http, _ = client
    admin = _token(http, 'admin')
    viewer = _token(http, 'viewer')
    study_id = _create_study(http, admin)
    response = http.post(f'/pairing/studies/{study_id}/corrections', headers=viewer,
                         json={'readings': [{'booklet': 'initial', 'source_key': 'I1',
                                             'sampled_at': 0, 'moisture': '1.0'}]})
    assert response.status_code == 403
    response = http.post(f'/pairing/studies/{study_id}/runs', headers=viewer,
                         json={'name': '越权', 'tolerance_minutes': 10})
    assert response.status_code == 403
    # 观察员可以读
    assert http.get(f'/pairing/studies/{study_id}/readings', headers=viewer).status_code == 200


def test_full_flow_bounds_and_review(client):
    http, Session = client
    analyst = _token(http, 'analyst')
    admin = _token(http, 'admin')
    study_id = _create_study(http, analyst, '同成本不同中位数')
    _correct(http, analyst, study_id, [
        {'booklet': 'initial', 'source_key': 'I0', 'sampled_at': 15, 'moisture': '2.2'},
        {'booklet': 'initial', 'source_key': 'I1', 'sampled_at': 10, 'moisture': '0.4'},
        {'booklet': 'retest', 'source_key': 'R0', 'sampled_at': 6, 'moisture': '0.2'},
        {'booklet': 'retest', 'source_key': 'R1', 'sampled_at': 13, 'moisture': '2.9'},
        {'booklet': 'retest', 'source_key': 'R2', 'sampled_at': 6, 'moisture': '2.0'},
    ])
    response = http.post(f'/pairing/studies/{study_id}/runs', headers=analyst,
                         json={'name': '杯签配对 · 首轮', 'tolerance_minutes': 8})
    assert response.status_code == 200, response.text
    run = response.json()
    assert run['status'] == 'queued'
    assert run['frozen_revision'] == 1

    with Session() as db:
        pairing_runs.calculate_run(db, run['id'])

    response = http.get(f'/pairing/runs/{run["id"]}', headers=analyst)
    assert response.status_code == 200
    data = response.json()
    assert data['status'] == 'completed'
    result = data['result']
    assert result['cardinality'] == 2
    assert result['total_time_diff'] == 6
    assert result['bound_lower']['median'] == '0.25'
    assert result['bound_upper']['median'] == '1.15'
    assert result['verified'] is True
    assert result['bound_lower']['pairing'] != result['bound_upper']['pairing']

    # 分析师不能复核
    assert http.post(f'/pairing/runs/{run["id"]}/review', headers=analyst,
                     json={'note': '分析师尝试复核'}).status_code == 403
    assert http.post(f'/pairing/runs/{run["id"]}/review', headers=admin,
                     json={'note': '配对范围与边界已核对，进入下一步复测方案'}).status_code == 200
    assert http.post(f'/pairing/runs/{run["id"]}/review', headers=admin,
                     json={'note': '重复复核应被拒绝'}).status_code == 409


def test_no_pairing_returns_reason(client):
    http, Session = client
    analyst = _token(http, 'analyst')
    study_id = _create_study(http, analyst, '无配对场景')
    _correct(http, analyst, study_id, [
        {'booklet': 'initial', 'source_key': 'I1', 'sampled_at': 0, 'moisture': '1.0'},
        {'booklet': 'retest', 'source_key': 'R1', 'sampled_at': 900, 'moisture': '1.2'},
    ])
    run = http.post(f'/pairing/studies/{study_id}/runs', headers=analyst,
                    json={'name': '无配对', 'tolerance_minutes': 30}).json()
    with Session() as db:
        pairing_runs.calculate_run(db, run['id'])
    data = http.get(f'/pairing/runs/{run["id"]}', headers=analyst).json()
    assert data['status'] == 'completed'
    assert data['result']['cardinality'] == 0
    assert data['result']['bound_lower'] is None
    assert '没有任何' in data['result']['reason']


def test_invalid_tolerance_is_rejected_before_queue(client):
    http, _ = client
    analyst = _token(http, 'analyst')
    study_id = _create_study(http, analyst, '非法时差')
    _correct(http, analyst, study_id, [
        {'booklet': 'initial', 'source_key': 'I1', 'sampled_at': 0, 'moisture': '1.0'},
        {'booklet': 'retest', 'source_key': 'R1', 'sampled_at': 0, 'moisture': '1.2'},
    ])
    response = http.post(f'/pairing/studies/{study_id}/runs', headers=analyst,
                         json={'name': '非法时差', 'tolerance_minutes': 0})
    assert response.status_code == 422
    # 没有任何运行被创建
    assert http.get('/pairing/runs', headers=analyst).json() == []


def test_duplicate_key_in_one_correction_rejected(client):
    http, _ = client
    analyst = _token(http, 'analyst')
    study_id = _create_study(http, analyst, '重复编号')
    response = http.post(f'/pairing/studies/{study_id}/corrections', headers=analyst, json={'readings': [
        {'booklet': 'initial', 'source_key': 'I1', 'sampled_at': 0, 'moisture': '1.0'},
        {'booklet': 'initial', 'source_key': 'I1', 'sampled_at': 1, 'moisture': '1.1'},
    ]})
    assert response.status_code == 422
