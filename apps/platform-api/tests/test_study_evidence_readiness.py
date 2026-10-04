from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import candidate_probe, study_evidence_readiness as readiness


class MissingObject(Exception):
    def get_error_code(self):
        return "NoSuchKey"


@pytest.fixture
def storage(monkeypatch):
    monkeypatch.setenv("STUDY_EVIDENCE_CLEANUP_ENABLED", "true")
    monkeypatch.setenv("STUDY_EVIDENCE_CLEANUP_TOKEN", "synthetic-only-token-" * 3)
    result = SimpleNamespace(backend="cloudbase", credential_mode="runtime",
        bucket="synthetic-bucket", region="ap-shanghai", client=MagicMock(),
        make_key=MagicMock(return_value="study-meetings/test/2026/10/" + "a" * 32 + ".jpg"),
        put=MagicMock(), get=MagicMock(), delete=MagicMock(), exists=MagicMock(return_value=False))
    result.client.head_object.side_effect = MissingObject()
    result.get.side_effect = lambda _: result.put.call_args.args[1]
    return result


def test_private_synthetic_object_is_read_back_and_removed(storage):
    urls = []
    result = readiness.check(storage_factory=lambda: storage,
        anonymous_status=lambda url: urls.append(url) or 403)
    assert result["status"] == "passed"
    assert all(result[k] for k in ("put", "get", "delete", "deleted_verified", "private"))
    assert len(urls) == 1 and "study-meetings/test/" in urls[0]
    storage.make_key.assert_called_once_with(session_id=0, extension="jpg")
    assert storage.put.call_args.args[1].startswith(b"\xff\xd8")
    assert "synthetic-bucket" not in str(result) and "synthetic-only-token" not in str(result)


@pytest.mark.parametrize("failure", ["public", "corrupt", "put_timeout", "delete_denied", "still_exists"])
def test_failed_checks_never_pass_and_compensate_only_the_created_key(storage, failure):
    if failure == "corrupt": storage.get.side_effect = lambda _: b"wrong"
    if failure == "put_timeout": storage.put.side_effect = RuntimeError("private-secret-details")
    if failure == "delete_denied": storage.delete.side_effect = RuntimeError("private-secret-details")
    if failure == "still_exists": storage.exists.return_value = True
    result = readiness.check(storage_factory=lambda: storage,
        anonymous_status=lambda _: 200 if failure == "public" else 403)
    assert result["status"] == "failed"
    assert "private-secret-details" not in str(result)
    assert storage.delete.called
    assert all(c.args == (storage.make_key.return_value,) for c in storage.delete.call_args_list)
    if failure == "delete_denied":
        assert result["compensation_failed"] is True
        assert result["synthetic_key"] == storage.make_key.return_value


def test_missing_cleanup_configuration_performs_no_object_write(monkeypatch):
    monkeypatch.setenv("STUDY_EVIDENCE_CLEANUP_ENABLED", "false")
    factory = MagicMock()
    assert readiness.check(storage_factory=factory)["status"] == "failed"
    factory.assert_not_called()


def test_storage_failure_blocks_the_candidate_probe(monkeypatch):
    monkeypatch.setenv("STUDY_MEETING_EVIDENCE_ENABLED", "true")
    monkeypatch.setattr(readiness, "check", lambda: {"status": "failed"})
    from app.core.build_info import get_build_info
    proof = candidate_probe.check("x" * 43, wait_for_socket=False,
        get=lambda path, _: get_build_info() if path.endswith("build-info") else {"status": "ok"})
    assert proof["status"] == "failed" and proof["http_requests"] == 23
    assert proof["study_evidence"]["status"] == "failed"
