from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.api.auth import current_user
from app.main import app
from app.services import credit_settlement_setup as service
from app.services.production_operations import REQUIRED_PERMISSION

BASE = '/api/v1/ops/production-actions/credit-settlement-storage'


def test_unauthenticated_storage_preparation_is_rejected():
    with TestClient(app) as client:
        assert client.get(BASE + '/preflight').status_code == 401
        assert client.post(BASE + '/0064/prepare', json={}).status_code == 401


def test_no_permission_cannot_read_or_prepare_storage():
    app.dependency_overrides[current_user] = lambda: {'id': 9, 'roles': [], 'permissions': []}
    try:
        with TestClient(app) as client:
            assert client.get(BASE + '/preflight').status_code == 403
            assert client.post(BASE + '/0064/prepare', json={}).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_extra_sql_and_nonallowlisted_migration_never_reach_service(monkeypatch):
    prepare = Mock()
    monkeypatch.setattr(service, 'prepare', prepare)
    app.dependency_overrides[current_user] = lambda: {'id': 1, 'roles': ['system_admin'], 'permissions': [REQUIRED_PERMISSION]}
    payload = {'expected_release_commit': 'a' * 40, 'expected_baseline_fingerprint': 'b' * 64,
               'expected_migration_sha256': 'c' * 64, 'execution_reason': '隔离验证固定迁移边界'}
    try:
        with TestClient(app) as client:
            assert client.post(BASE + '/0064/prepare', json={**payload, 'sql': 'SELECT 1'}).status_code == 422
            assert client.post(BASE + '/0069/prepare', json=payload).status_code == 422
        prepare.assert_not_called()
    finally:
        app.dependency_overrides.clear()
