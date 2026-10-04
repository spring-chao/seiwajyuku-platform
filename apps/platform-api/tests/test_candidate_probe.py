import io
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from app.main import app
from app.services import candidate_probe
from app.api import system


def test_candidate_probe_uses_real_health_query_twenty_times(monkeypatch, caplog):
    reads = []
    original = system.fetch_one
    def read(sql):
        reads.append(sql)
        return original(sql)
    monkeypatch.setattr(system, "fetch_one", read)
    requests = []
    with TestClient(app) as client:
        def local_http(url, timeout):
            parsed = urlsplit(url)
            assert parsed.hostname == "127.0.0.1" and parsed.port == 8000
            response = client.get(parsed.path + "?" + parsed.query)
            requests.append(parsed.path)
            stream = io.BytesIO(response.content)
            stream.status = response.status_code
            return stream
        monkeypatch.setattr(candidate_probe, "urlopen", local_http)
        caplog.set_level("INFO", logger="uvicorn.error")
        proof = candidate_probe.check("x" * 43, wait_for_socket=False)
    assert proof["status"] == "passed" and proof["http_requests"] == 23
    assert proof["database_health_passed"] == 20
    assert reads == ["SELECT 1 AS ok"] * 20
    assert len(requests) == 23
    assert "x" * 43 not in caplog.text


def test_failed_check_never_reports_pass_or_leaks_exception_text(caplog):
    caplog.set_level("INFO", logger="uvicorn.error")
    def failed_get(*_):
        raise RuntimeError("private database password must not escape")
    proof = candidate_probe.check("x" * 43, get=failed_get, wait_for_socket=False)
    assert proof["status"] == "failed" and proof["database_health_passed"] == 0
    assert "private database password" not in caplog.text


def test_probe_configuration_is_optional_and_rejects_arbitrary_urls(monkeypatch):
    for value in ("", "http://example.test", "x" * 120):
        monkeypatch.setenv(candidate_probe.PROBE_ENV, value)
        assert candidate_probe.configured_id() is None
    monkeypatch.setenv(candidate_probe.PROBE_ENV, "x" * 43)
    assert candidate_probe.configured_id() == "x" * 43
