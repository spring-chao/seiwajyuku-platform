from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.db import execute, fetch_one, transaction
from app.main import app
from app.services import integrations, legacy_operations_merge as legacy
from app.services import participation_history_import as service


API_KEY = "test-history-integration-key"
BASE = "/api/v1/integrations/participation-history"
REASON = "合成隔离测试核对通过的历史活动导入"


@pytest.fixture
def history_import(monkeypatch):
    settings = SimpleNamespace(integration_api_key=API_KEY, is_production=False,
                               deployment_read_only=False, allow_production_mutations=False)
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(integrations, "get_settings", lambda: settings)
    monkeypatch.setattr(service, "APPROVED_IMPORT_SCOPES", ())
    code = "M2M-HISTORY-" + uuid4().hex
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        member_id = execute(connection,
            "INSERT INTO members(member_code, name, org_unit_id, status, created_at, updated_at) "
            "VALUES (?, '接口隔离测试学员', 'org-suzhou', 'ACTIVE', ?, ?)",
            (code, now, now)).lastrowid
    fact = {"source_table": "courses", "external_id": "workbook-" + uuid4().hex,
            "member_code": code, "occurred_on": "2025-01-01",
            "participation_status": "PRESENT", "title": "合成历史课程"}

    def bundle(facts=None, **changes):
        payload = {"bundle_version": 1, "source_system": "activity_workbooks",
                   "generated_at": "2026-01-01T00:00:00+00:00",
                   "privacy_contract": dict(service.WORKBOOK_PRIVACY),
                   "facts": facts if facts is not None else [dict(fact)]}
        payload.update(changes)
        return json.dumps(payload, ensure_ascii=False).encode()

    def approve(content, count=1, deadline=None):
        scope = service.ParticipationHistoryImportScope(
            legacy.bundle_sha256(content), count, deadline or datetime.now(UTC) + timedelta(hours=1))
        monkeypatch.setattr(service, "APPROVED_IMPORT_SCOPES", (scope,))
        return scope

    with TestClient(app) as client:
        yield SimpleNamespace(client=client, settings=settings, member_id=member_id,
                              fact=fact, bundle=bundle, approve=approve)


def request(case, operation, content, headers=None, **form):
    data = {"confirmation_reason": REASON, "second_confirmed": "true"} if operation == "apply" else {}
    data.update(form)
    return case.client.post(BASE + "/" + operation, headers=headers if headers is not None else {"X-API-Key": API_KEY},
                            files={"file": ("history.json", content, "application/json")}, data=data)


def counts(content):
    digest = legacy.bundle_sha256(content)
    return {
        "batches": fetch_one("SELECT count(*) AS n FROM import_batches WHERE source_sha256=?", (digest,))["n"],
        "facts": fetch_one("SELECT count(*) AS n FROM member_activity_facts WHERE import_batch_id IN "
                          "(SELECT id FROM import_batches WHERE source_sha256=?)", (digest,))["n"],
        "audits": fetch_one("SELECT count(*) AS n FROM audit_logs WHERE action='integrations.participation_history.apply' "
                           "AND resource_id IN (SELECT CAST(id AS CHAR) FROM import_batches WHERE source_sha256=?)", (digest,))["n"],
    }


def test_machine_identity_requires_api_key(history_import):
    case = history_import
    content = case.bundle()
    case.approve(content)
    for headers in ({}, {"X-API-Key": "wrong-test-key"}, {"Authorization": "Bearer not-an-api-key"}):
        response = request(case, "apply", content, headers=headers)
        assert response.status_code == 401
    assert counts(content) == {"batches": 0, "facts": 0, "audits": 0}


def test_production_rejects_default_development_identity(history_import):
    case = history_import
    case.settings.is_production = True
    case.settings.integration_api_key = "dev-integration-key"
    content = case.bundle()
    case.approve(content)
    assert request(case, "preview", content, headers={"X-API-Key": "dev-integration-key"}).status_code == 401
    assert counts(content) == {"batches": 0, "facts": 0, "audits": 0}


def test_machine_scope_is_closed_by_default(history_import):
    case = history_import
    content = case.bundle()
    for operation in ("preview", "apply"):
        assert request(case, operation, content).status_code == 403
    assert counts(content) == {"batches": 0, "facts": 0, "audits": 0}


def test_machine_upload_over_twenty_megabytes_is_rejected(history_import):
    case = history_import
    content = b"x" * (20 * 1024 * 1024 + 1)
    response = request(case, "preview", content)
    assert response.status_code == 400
    assert "20MB" in response.json()["detail"]
    assert counts(content) == {"batches": 0, "facts": 0, "audits": 0}


@pytest.mark.parametrize("change", ["sha", "count", "deadline"])
def test_server_approval_binds_sha_count_and_deadline(history_import, change):
    case = history_import
    content = case.bundle()
    if change == "sha":
        case.approve(case.bundle(generated_at="2026-01-02T00:00:00+00:00"))
    elif change == "count":
        case.approve(content, count=2)
    else:
        case.approve(content, deadline=datetime.now(UTC) - timedelta(seconds=1))
    response = request(case, "apply", content)
    assert response.status_code == (400 if change == "count" else 403)
    assert counts(content) == {"batches": 0, "facts": 0, "audits": 0}


def test_client_cannot_supply_its_own_approval(history_import):
    case = history_import
    content = case.bundle(approval={"bundle_sha256": "client-declared", "verified_fact_count": 1})
    assert request(case, "apply", content).status_code == 403
    case.approve(content)
    assert request(case, "apply", content).status_code == 400
    assert counts(content) == {"batches": 0, "facts": 0, "audits": 0}


def test_preview_has_no_writes_and_apply_records_service_actor(history_import):
    case = history_import
    content = case.bundle()
    case.approve(content)
    preview = request(case, "preview", content)
    assert preview.status_code == 200
    assert preview.json()["data"]["source_sha256"] == legacy.bundle_sha256(content)
    assert preview.json()["data"]["summary"]["importable"] == 1
    assert "接口隔离测试学员" not in preview.text
    assert counts(content) == {"batches": 0, "facts": 0, "audits": 0}
    result = request(case, "apply", content)
    assert result.status_code == 200
    batch = result.json()["data"]["batch_id"]
    assert counts(content) == {"batches": 1, "facts": 1, "audits": 1}
    assert fetch_one("SELECT created_by FROM import_batches WHERE id=?", (batch,))["created_by"] is None
    audit = fetch_one("SELECT actor_user_id, after_json FROM audit_logs WHERE "
                     "action='integrations.participation_history.apply' AND resource_id=?", (str(batch),))
    assert audit["actor_user_id"] is None
    after = json.loads(audit["after_json"])
    assert after["actor_type"] == "SERVICE" and after["authentication"] == "INTEGRATION_API_KEY"
    assert API_KEY not in audit["after_json"]
    assert request(case, "apply", content).status_code == 400
    post = request(case, "preview", content)
    assert post.status_code == 200
    assert post.json()["data"]["summary"]["duplicates"] == 1
    assert counts(content) == {"batches": 1, "facts": 1, "audits": 1}


def test_all_six_workbook_categories_share_the_transaction(history_import):
    case = history_import
    facts = [{**case.fact, "source_table": table, "external_id": "workbook-" + uuid4().hex}
             for table in sorted(service.WORKBOOK_TABLES)]
    content = case.bundle(facts)
    case.approve(content, count=6)
    result = request(case, "apply", content)
    assert result.status_code == 200
    assert result.json()["data"]["by_activity_type"] == {
        "LEARNING_MEETING": 1, "CLASS_STUDY_DAY": 1, "GROUP_SESSION": 1,
        "CLASS_OPENING": 1, "COURSE": 1, "OTHER_ACTIVITY": 1}
    assert counts(content) == {"batches": 1, "facts": 6, "audits": 1}


@pytest.mark.parametrize("change", [
    {"participation_status": "ABSENT"},
    {"source_table": "reading_shares"},
    {"occurred_on": "2024-12-31"},
    {"occurred_on": "2026-12-31"},
    {"occurred_on": "2025-02-30"},
    {"score": 10},
    {"org_unit_id": "another-org"},
    {"name": "不应上传的人员姓名"},
])
def test_machine_scope_cannot_import_other_fields_statuses_or_dates(history_import, change, monkeypatch):
    case = history_import
    if change == {"occurred_on": "2026-12-31"}:
        class HistoricalTestClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 1, 1, tzinfo=UTC).astimezone(tz or UTC)
        monkeypatch.setattr(service, "datetime", HistoricalTestClock)
    content = case.bundle([{**case.fact, **change}])
    case.approve(content)
    assert request(case, "apply", content).status_code == 400
    assert counts(content) == {"batches": 0, "facts": 0, "audits": 0}


def test_machine_scope_cannot_use_legacy_source(history_import):
    case = history_import
    content = case.bundle(source_system="seiwajyuku_system")
    case.approve(content)
    assert request(case, "apply", content).status_code == 400
    assert counts(content) == {"batches": 0, "facts": 0, "audits": 0}


def test_unknown_member_is_rejected_even_for_existing_external_key(history_import):
    case = history_import
    original = case.bundle()
    case.approve(original)
    assert request(case, "apply", original).status_code == 200
    content = case.bundle([{**case.fact, "member_code": "NOT-A-KNOWN-TEST-MEMBER"}])
    case.approve(content)
    for operation in ("preview", "apply"):
        assert request(case, operation, content).status_code == 400
    assert counts(content) == {"batches": 0, "facts": 0, "audits": 0}
    assert counts(original) == {"batches": 1, "facts": 1, "audits": 1}


def test_batch_facts_and_audit_roll_back_together(history_import, monkeypatch):
    case = history_import
    content = case.bundle()
    case.approve(content)
    def fail_audit(*args, **kwargs):
        raise RuntimeError("synthetic audit failure")
    monkeypatch.setattr(legacy, "write_audit", fail_audit)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        request(case, "apply", content)
    assert counts(content) == {"batches": 0, "facts": 0, "audits": 0}


def test_apply_still_requires_confirmation_and_reason(history_import):
    case = history_import
    content = case.bundle()
    case.approve(content)
    assert request(case, "apply", content, second_confirmed="false").status_code == 400
    assert request(case, "apply", content, confirmation_reason="短原因").status_code == 422
    assert counts(content) == {"batches": 0, "facts": 0, "audits": 0}


@pytest.mark.parametrize("read_only", [False, True])
def test_service_apply_preserves_production_mutation_gates(history_import, read_only):
    case = history_import
    case.settings.is_production = True
    case.settings.deployment_read_only = read_only
    case.settings.allow_production_mutations = read_only
    content = case.bundle()
    case.approve(content)
    assert request(case, "apply", content).status_code == 403
    assert counts(content) == {"batches": 0, "facts": 0, "audits": 0}


def test_human_entry_does_not_accept_machine_key_or_null_actor(history_import):
    case = history_import
    content = case.bundle()
    case.approve(content)
    response = case.client.post("/api/v1/legacy-operations/apply", headers={"X-API-Key": API_KEY},
        files={"file": ("history.json", content, "application/json")},
        data={"confirmation_reason": REASON, "second_confirmed": "true"})
    assert response.status_code == 401
    with pytest.raises(PermissionError):
        legacy.apply_bundle(content, "history.json", None, REASON, True)
    assert counts(content) == {"batches": 0, "facts": 0, "audits": 0}


def test_deadline_is_checked_inside_the_write_transaction(history_import, monkeypatch):
    case = history_import
    content = case.bundle()
    scope = case.approve(content)
    class ExpiredClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return scope.deadline_utc + timedelta(seconds=1)
    monkeypatch.setattr(legacy, "datetime", ExpiredClock)
    assert request(case, "apply", content).status_code == 403
    assert counts(content) == {"batches": 0, "facts": 0, "audits": 0}


def test_concurrent_all_duplicate_package_creates_only_one_batch(history_import):
    case = history_import
    original = case.bundle()
    case.approve(original)
    assert request(case, "apply", original).status_code == 200
    content = case.bundle(generated_at="2026-01-02T00:00:00+00:00")
    case.approve(content)
    def apply_once():
        try:
            return service.apply_workbook_bundle(content, "history.json", API_KEY, REASON, True)
        except ValueError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: apply_once(), range(2)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, str) and "已经执行" in result for result in results) == 1
    assert counts(content) == {"batches": 1, "facts": 0, "audits": 1}
