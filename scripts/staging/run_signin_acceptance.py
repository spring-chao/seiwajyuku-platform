"""Exercise two actual loopback HTTP services using only synthetic fixtures.

SQLite validates this service/data-flow acceptance, never MySQL compatibility.
All credentials are newly generated test values; no deployed service is used.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from cryptography.fernet import Fernet


ROOT = Path(__file__).resolve().parents[2]
API_ROOT = ROOT / "apps/platform-api"
sys.path.insert(0, str(API_ROOT))
PLATFORM = "http://127.0.0.1:8765"
ENGINE = "http://127.0.0.1:8766"
PERMISSIONS = ["attendance:view", "attendance:create", "attendance:update", "attendance:manage",
               "attendance:status", "attendance:import", "attendance:export", "attendance:code"]


def require_free(port: int) -> None:
    with socket.socket() as connection:
        if connection.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"Owned staging port {port} is already occupied; do not stop unrelated services")


def configure(output: Path) -> dict:
    database = output / "loopback-synthetic.db"
    if database.exists():
        raise RuntimeError("Use a new output directory; never silently overwrite an acceptance database")
    operator_password = secrets.token_urlsafe(18)
    env = {
        "APP_ENV": "test", "DATABASE_URL": "sqlite:///" + database.as_posix(),
        "ALLOW_PRODUCTION_MUTATIONS": "false", "DEPLOYMENT_READ_ONLY": "false",
        "RUN_BOOTSTRAP_ON_STARTUP": "false", "JWT_SECRET": secrets.token_urlsafe(40),
        "FIELD_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "SIGNIN_MANAGEMENT_ENABLED": "true", "SIGNIN_MEMBER_CHECKIN_ENABLED": "true",
        "SIGNIN_API_BASE_URL": ENGINE, "SIGNIN_PLATFORM_API_KEY": secrets.token_urlsafe(36),
        "SIGNIN_SERVICE_API_KEY": secrets.token_urlsafe(36),
        "WECHAT_MEMBER_BINDING_ENABLED": "true", "WECHAT_LOCAL_TEST_MODE": "true",
        "IDENTITY_AUTHORIZATION_ENABLED": "true", "IDENTITY_ADMIN_WRITES_ENABLED": "true",
        "CORS_ORIGINS": "http://127.0.0.1:5184,http://localhost:5184",
        "BOOTSTRAP_ADMIN_USERNAME": "loopback-bootstrap", "BOOTSTRAP_ADMIN_PASSWORD": secrets.token_urlsafe(20),
        "SIGNIN_LEGACY_URL": "https://signin.example.invalid/index.html",
        "CHECKIN_ROSTER_API_BASE": PLATFORM, "CHECKIN_ROSTER_API_KEY": "", "SIGNIN_STAGING_PORT": "8766",
    }
    env["CHECKIN_ROSTER_API_KEY"] = env["SIGNIN_SERVICE_API_KEY"]
    os.environ.update(env)
    from app.migrations import run_migrations
    from app.services.iam import seed_iam
    from app.core.privacy import protected_phone
    from app.core.security import hash_password
    from app.db import execute, transaction

    run_migrations()
    seed_iam()
    now = datetime.now(UTC).isoformat()
    fixture = {"synthetic_only": True, "platform_url": PLATFORM, "engine_url": ENGINE,
               "operator": {"username": "loopback-operator", "password": operator_password},
               "member": {"name": "合成学员甲", "phone": "13800009001", "member_code": "LOOPBACK-MEMBER-1"},
               "center_id": "loopback-center", "class_id": "loopback-class", "group_id": "loopback-group"}
    with transaction() as connection:
        for org_id, name, unit_type, parent in [(fixture["center_id"], "隔离验收中心", "REGIONAL_CENTER", "org-suzhou"),
                                               (fixture["class_id"], "隔离验收班", "CLASS", fixture["center_id"]),
                                               (fixture["group_id"], "隔离验收小组", "GROUP", fixture["class_id"])]:
            execute(connection, "INSERT INTO org_units(id,unit_code,name,unit_type,parent_id,is_active,created_at,updated_at) VALUES (?,?,?,?,?,1,?,?)",
                    (org_id, org_id.upper(), name, unit_type, parent, now, now))
        for index in [1, 2]:
            fields = protected_phone(fixture["member"]["phone"] if index == 1 else "13800009002")
            mid = execute(connection, "INSERT INTO members(member_code,name,org_unit_id,status,phone_ciphertext,phone_hash,phone_last4,phone_masked,created_at,updated_at) VALUES (?,?,?,'ACTIVE',?,?,?,?,?,?)",
                          (f"LOOPBACK-MEMBER-{index}", fixture["member"]["name"], fixture["class_id"], fields["phone_ciphertext"], fields["phone_hash"], fields["phone_last4"], fields["phone_masked"], now, now)).lastrowid
            if index == 1:
                fixture["member"]["member_id"] = mid
            for org_id, relation in [(fixture["class_id"], "STUDY_CLASS"), (fixture["group_id"], "STUDY_GROUP")]:
                execute(connection, "INSERT INTO member_org_relations(member_id,org_unit_id,relation_type,is_primary,created_at,updated_at) VALUES (?,?,?,1,?,?)", (mid, org_id, relation, now, now))
        uid = execute(connection, "INSERT INTO app_users(username,display_name,password_hash,is_active,token_version,created_at,updated_at) VALUES (?,?,?,1,1,?,?)",
                      (fixture["operator"]["username"], "隔离验收运营员", hash_password(operator_password), now, now)).lastrowid
        role = "loopback-attendance-operator"
        execute(connection, "INSERT INTO roles(role_key,role_name,is_system,is_active,created_at,updated_at) VALUES (?,?,0,1,?,?)", (role, "隔离验收签到角色", now, now))
        for permission in PERMISSIONS:
            execute(connection, "INSERT INTO role_permissions(role_key,permission_key) VALUES (?,?)", (role, permission))
        execute(connection, "INSERT INTO user_roles(user_id,role_key,created_at) VALUES (?,?,?)", (uid, role, now))
        execute(connection, "INSERT INTO data_scope_grants(user_id,scope_type,org_unit_id,created_at) VALUES (?,'SUBTREE',?,?)", (uid, fixture["center_id"], now))
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    start = datetime.now(UTC) - timedelta(hours=1)
    end = datetime.now(UTC) + timedelta(hours=3)
    seed = {"events": [], "registrations": [], "checkins": [], "event_audit_logs": [], "config": []}
    fixture["event_ids"] = []
    for suffix, code, title in [("morning", "MORNING", "隔离三场上午"), ("afternoon", "AFTERNOON", "隔离三场下午"), ("konpa", "KONPA", "隔离三场空巴"), ("outage", "MORNING", "隔离停机直签"),
                                ("team", "MORNING", "隔离团队备用"), ("removal", "MORNING", "隔离报名删除")]:
        eid = "loopback-" + suffix
        fixture["event_ids"].append(eid)
        triple = suffix in {"morning", "afternoon", "konpa"}
        seed["events"].append({"_id": "doc-" + eid, "event_id": eid, "event_group_id": "loopback-triple" if triple else eid,
                               "name": title, "event_date": today, "activity_type": "class_meeting" if triple else "course",
                               "session_code": code, "status": "active", "lifecycle_status": "CONFIRMED", "org_unit_id": fixture["class_id"],
                               "class_org_unit_id": fixture["class_id"], "group_org_unit_id": fixture["group_id"], "source_system": "PLATFORM_MANAGEMENT",
                               "checkin_start_at": start.isoformat(), "checkin_end_at": end.isoformat(), "scheduled_start_at": start.isoformat(), "scheduled_end_at": end.isoformat(), "created_at": now})
        for index in [1, 2]:
            member_index = 1 if suffix == "team" else index
            seed["registrations"].append({"_id": f"reg-{suffix}-{index}", "batch_id": eid, "event_group_id": seed["events"][-1]["event_group_id"],
                                          "name": fixture["member"]["name"], "registered_name": fixture["member"]["name"], "actual_attendee_name": "",
                                          "platform_member_id": str(fixture["member"]["member_id"] + member_index - 1), "member_code": f"LOOPBACK-MEMBER-{member_index}",
                                          "attendance_role": "COURSE_TEAM_MEMBER" if suffix == "team" else "CLASS_MEMBER", "participant_type": "TEAM" if suffix == "team" else "MEMBER",
                                          "company": "合成团队" if suffix == "team" else "", "score_eligible": suffix != "team", "attendance_status": "pending", "created_at": now})
    seed_path = output / "engine-synthetic-seed.json"
    seed_path.write_text(json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8")
    env["SIGNIN_STAGING_SEED_FILE"] = str(seed_path)
    os.environ.update(env)
    (output / "loopback-private-fixture.json").write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
    return fixture


def wait_ready(url: str, timeout: float = 30) -> None:
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.15)
    raise RuntimeError("Loopback service failed to become ready")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signin-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--keep-running", action="store_true")
    args = parser.parse_args()
    require_free(8765); require_free(8766)
    args.output.mkdir(parents=True, exist_ok=True)
    fixture = configure(args.output)
    processes: list[subprocess.Popen] = []
    logs = []

    def launch_api():
        log = (args.output / "platform-loopback.log").open("a", encoding="utf-8")
        logs.append(log)
        child = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8765", "--no-access-log"], cwd=API_ROOT, env=dict(os.environ), stdout=log, stderr=subprocess.STDOUT)
        processes.append(child); wait_ready(PLATFORM + "/openapi.json")
        return child

    try:
        engine_log = (args.output / "engine-loopback.log").open("w", encoding="utf-8")
        logs.append(engine_log)
        engine = subprocess.Popen(["node", "scripts/staging/serve-engine.js"], cwd=args.signin_root, env=dict(os.environ), stdout=engine_log, stderr=subprocess.STDOUT)
        processes.append(engine); wait_ready(ENGINE + "/__staging__/snapshot")
        api = launch_api()
        with httpx.Client(timeout=20) as client:
            def request(method, path, *, base=PLATFORM, headers=None, body=None, status=200):
                response = client.request(method, base + path, headers=headers, json=body)
                if response.status_code != status:
                    raise RuntimeError(f"Expected {status} for {method} {path}, got {response.status_code}: {response.text[:400]}")
                return response.json()

            login = request("POST", "/api/v1/auth/login", body=fixture["operator"])
            admin_headers = {"Authorization": "Bearer " + login["data"]["access_token"]}
            member = request("POST", "/api/v1/wechat/person-bindings/verify", body={"wx_login_code": "loopback-login", "name": fixture["member"]["name"], "phone": fixture["member"]["phone"]})
            member_headers = {"Authorization": "Bearer " + member["data"]["access_token"]}
            checks = []
            events = request("GET", "/api/v1/wechat/checkin/events", headers=member_headers)["data"]["events"]
            assert set(fixture["event_ids"]).issubset({event["event_id"] for event in events})
            checks.append("current_events_and_same_name_identity")
            for eid in fixture["event_ids"][:3]:
                state = request("GET", "/api/v1/wechat/checkin/context?event_id=" + eid, headers=member_headers)["data"]
                assert state["can_checkin"] and state["member"]["member_id"] == fixture["member"]["member_id"]
                assert state["registration"]["registration_id"].endswith("-1")
                first = request("POST", "/api/v1/wechat/checkin/confirm", headers=member_headers, body={"event_id": eid})["data"]
                assert first["status"] == "CHECKED_IN" and first["sync_status"] == "SYNCED"
                duplicate = request("POST", "/api/v1/wechat/checkin/confirm", headers=member_headers, body={"event_id": eid})["data"]
                assert duplicate["status"] == "ALREADY_CHECKED_IN"
            snapshot = request("GET", "/__staging__/snapshot", base=ENGINE)
            assert len(snapshot["checkins"]) == 3 and len({row["batch_id"] for row in snapshot["checkins"]}) == 3
            checks.append("three_independent_sessions_and_idempotence")
            from app.db import transaction
            with transaction() as connection:
                runs = connection.execute("SELECT received_sessions, received_records, status FROM attendance_sync_runs ORDER BY id").fetchall()
                attendance_count = connection.execute("SELECT COUNT(*) FROM attendance_records").fetchone()[0]
            assert len(runs) == 3 and all(row[0] == 1 and row[1] == 1 and row[2] == "SUCCESS" for row in runs)
            assert attendance_count == 3, "Immediate sync imports the checked registration only"
            checks.append("immediate_sync_pulls_exactly_one_registration_each_time")
            history = request("GET", "/api/v1/wechat/participation-history?kind=learning", headers=member_headers)["data"]
            assert history["total"] == 1, "One activity group counts once despite three session facts"
            checks.append("actual_attendance_sync_and_personal_history_group_deduplication")
            key_headers = {"X-API-Key": os.environ["SIGNIN_SERVICE_API_KEY"]}
            request("POST", "/api/v1/attendance/sync/immediate", headers=key_headers,
                    body={"event_id": "loopback-morning", "registration_id": "nonexistent-registration"}, status=503)
            checks.append("target_registration_ack_rejects_missing_fact")
            details = request("POST", "/api/v1/attendance/manage/event_detail", headers=admin_headers, body={"event_id": "loopback-morning"})
            assert details.get("ok") is True and len(details.get("rows", [])) == 2
            checks.append("actual_management_engine_and_live_roster")
            team = request("GET", "/api/v1/wechat/checkin/context?event_id=loopback-team", headers=member_headers)["data"]
            assert team["requires_fallback"] and not team["can_checkin"] and not team.get("checkin_ticket")
            assert team["fallback_url"], "Team contacts retain the configured legacy identity workflow"
            request("POST", "/api/v1/wechat/checkin/confirm", headers=member_headers, body={"event_id": "loopback-team"}, status=409)
            checks.append("bound_team_slots_require_fallback_without_issuing_ticket_or_checkin")
            removal = request("GET", "/api/v1/wechat/checkin/context?event_id=loopback-removal", headers=member_headers)["data"]
            assert removal["can_checkin"] and removal.get("checkin_ticket")
            deleted = request("POST", "/api/v1/attendance/manage/registration_delete", headers=admin_headers,
                              body={"event_id": "loopback-removal", "registration_id": "reg-removal-1"})
            assert deleted["ok"] is True
            rejected = request("POST", "/native/v1/checkin/confirm", base=ENGINE, body={"ticket": removal["checkin_ticket"]}, status=409)
            assert rejected["ok"] is False and rejected["status"] == "NOT_REGISTERED"
            snapshot = request("GET", "/__staging__/snapshot", base=ENGINE)
            assert not any(row["batch_id"] in {"loopback-team", "loopback-removal"} for row in snapshot["checkins"])
            checks.append("preloaded_ticket_after_registration_removal_rejects_without_attendance_fact")
            outage = request("GET", "/api/v1/wechat/checkin/context?event_id=loopback-outage", headers=member_headers)["data"]
            ticket = outage.get("checkin_ticket")
            assert ticket, "The actual context must preload a signed ticket before outage"
            api.terminate(); api.wait(timeout=10)
            offline = request("POST", "/native/v1/checkin/confirm", base=ENGINE, body={"ticket": ticket})
            assert offline["ok"] is True and offline["sync_status"] == "PENDING"
            duplicate = request("POST", "/native/v1/checkin/confirm", base=ENGINE, body={"ticket": ticket})
            assert duplicate["ok"] is True and duplicate["already"] is True
            snapshot = request("GET", "/__staging__/snapshot", base=ENGINE)
            pending = [row for row in snapshot["checkins"] if row["batch_id"] == "loopback-outage"]
            assert len(pending) == 1 and pending[0]["sync_state"] == "PENDING"
            checks.append("preloaded_ticket_commit_with_platform_stopped_and_pending_outbox")
            api = launch_api()
            retry = request("POST", "/__staging__/retry", base=ENGINE, body={})
            assert retry["delivered_count"] >= 1, f"Actual retry summary: {retry}"
            snapshot = request("GET", "/__staging__/snapshot", base=ENGINE)
            assert [row for row in snapshot["checkins"] if row["batch_id"] == "loopback-outage"][0]["sync_state"] == "DELIVERED"
            history = request("GET", "/api/v1/wechat/participation-history?kind=learning", headers=member_headers)["data"]
            assert history["total"] == 2
            checks.append("restart_platform_retry_delivers_and_updates_personal_history")
            scheduled = request("POST", "/api/v1/attendance/sync/scheduled", headers=key_headers, body={})
            assert scheduled["success"] is True
            checks.append("scheduled_pull_fallback_preserved")
            evidence = {"status": "PASS", "checked_at_utc": datetime.now(UTC).isoformat(), "date_asia_shanghai": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
                        "platform_url": PLATFORM, "engine_url": ENGINE, "database": str(args.output / "loopback-synthetic.db"),
                        "synthetic_only": True, "production_operations": False, "http_services": "actual FastAPI + actual signin cloudfunc handler",
                        "engine_storage": "isolated process-local transactional CloudBase adapter", "schema_compatibility": "SQLite data-flow evidence; MySQL CI is separate",
                        "checks": checks, "checkin_facts": len(snapshot["checkins"]), "personal_learning_activity_groups": history["total"],
                        "phone_preview_acceptance": False, "live_ticket_values_saved": False, "service_pids": [api.pid, engine.pid]}
            (args.output / "loopback-acceptance-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"status": "PASS", "checks": checks, "evidence": str(args.output / "loopback-acceptance-evidence.json")}, ensure_ascii=False), flush=True)
        if args.keep_running:
            print("Synthetic loopback services remain available on 8765/8766; interrupt this runner to stop its children.", flush=True)
            while True:
                time.sleep(1)
    finally:
        for child in processes:
            if child.poll() is None:
                child.terminate(); child.wait(timeout=10)
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
