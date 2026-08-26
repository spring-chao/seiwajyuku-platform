"""Run the B2 learning-plan gate against an ephemeral MySQL database.

This is an integration harness for GitHub Actions, not a deployment command.
It deliberately uses the same ``import_plan`` and learning-cycle services as
the application, while keeping the source-workbook readback boundary explicit:
the proprietary workbooks are not checked into Git, so their already completed
local B2-1 verification is recorded as an attestation in the report.  The
checked-in review manifest, fixed source commit and JSON fingerprint are still
verified here before any rows are written.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "apps" / "platform-api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from learning_plan_2026 import import_plan, validate_plan  # noqa: E402
from review_learning_plan_2026 import (  # noqa: E402
    git_commit,
    sha256_file_matches,
)


EXPECTED_SOURCE_COMMIT = "370dc9410acf431451a4fb5c54d7436ba43f0338"
EXPECTED_SOURCE_JSON_SHA256 = "404e1a9b3ea5037c9d5dd01d112186a629243115ee5c64da8ae0602cd572e8a2"
EXPECTED_TRACKS = (1, 4, 7, 10)
EXPECTED_CYCLES = 144
EXPECTED_TASKS = 1819


def _fail(message: str) -> NoReturn:
    raise AssertionError(message)


def _assert_ci_target() -> dict[str, str]:
    """Fail closed unless the harness is pointed at local ephemeral MySQL."""

    if os.getenv("B2_MYSQL_VALIDATION") != "true":
        _fail("B2_MYSQL_VALIDATION 必须显式为 true")
    if os.getenv("ALLOW_PRODUCTION_MUTATIONS", "false").lower() == "true":
        _fail("CI-MYSQL 禁止 ALLOW_PRODUCTION_MUTATIONS=true")
    if os.getenv("DEPLOYMENT_READ_ONLY", "false").lower() == "true":
        _fail("B2 CI 需要在隔离数据库写入，不能使用只读部署")
    if os.getenv("IDENTITY_ADMIN_WRITES_ENABLED", "false").lower() == "true":
        _fail("CI-MYSQL 禁止身份写入门禁开启")

    database_url = os.getenv("DATABASE_URL", "")
    parsed = urlparse(database_url)
    hostname = (parsed.hostname or "").lower()
    database = parsed.path.lstrip("/").lower()
    if not database_url.startswith("mysql+pymysql://"):
        _fail("B2 MySQL 验证必须使用 mysql+pymysql://")
    if hostname not in {"127.0.0.1", "localhost", "mysql"}:
        _fail(f"数据库主机不在 CI 白名单: {hostname!r}")
    if not ("staging" in database or "ci" in database):
        _fail(f"数据库名称必须包含 staging 或 ci: {database!r}")
    lowered = database_url.lower()
    forbidden = ("shengheshu", "cloudbase", "production", "prod-")
    if any(token in lowered for token in forbidden):
        _fail("数据库地址疑似生产/CloudBase 资源，已拒绝")
    return {"app_env": os.getenv("APP_ENV", ""), "database_url": database_url}


def _load_source_attestation() -> tuple[dict, dict, dict]:
    plan_path = REPO_ROOT / "data" / "learning-plans" / "standard-3y-2026.json"
    review_path = REPO_ROOT / "data" / "learning-plans" / "standard-3y-2026.review.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    report = validate_plan(plan)
    if report["errors"]:
        _fail(f"B1 学习计划校验失败: {report['errors']}")
    if review.get("status") != "CONFIRMED":
        _fail("2026 审核清单不是 CONFIRMED")
    resolved_commit = git_commit(REPO_ROOT, EXPECTED_SOURCE_COMMIT)
    if resolved_commit != EXPECTED_SOURCE_COMMIT:
        _fail("固定审核提交无法在当前 checkout 解析")
    if review.get("source_commit") != EXPECTED_SOURCE_COMMIT:
        _fail("审核清单 source_commit 与固定基线不一致")
    if review.get("source_json") != plan_path.name:
        _fail("审核清单 source_json 文件名不一致")
    if review.get("source_json_sha256") != EXPECTED_SOURCE_JSON_SHA256:
        _fail("审核清单中的 JSON SHA-256 与固定基线不一致")
    if not sha256_file_matches(plan_path, EXPECTED_SOURCE_JSON_SHA256):
        _fail("当前 checkout 的标准 JSON SHA-256 与固定基线不一致")
    workbooks = review.get("source_workbooks") or {}
    if set(workbooks) != {"1", "2", "3"}:
        _fail("审核清单没有完整绑定三份原始 Excel 指纹")
    for year, entry in workbooks.items():
        if not isinstance(entry, dict) or not entry.get("file") or not entry.get("sha256"):
            _fail(f"第{year}年 Excel 指纹不完整")
    checkpoint_tasks = sum(int(item.get("task_count") or 0) for item in review.get("checkpoints", []))
    if checkpoint_tasks != 500:
        _fail(f"审核清单抽查任务数异常: {checkpoint_tasks}")
    attestation = {
        "mode": "PRIOR_LOCAL_B2_1_ATTESTATION",
        "status": "PASS",
        "checked_task_count": 500,
        "mismatch_count": 0,
        "note": "原始 Excel 不入 Git；500 项源单元格回读已在本地 B2-1 完成，本工作流只验证固定指纹与审核清单。",
        "source_workbooks": workbooks,
    }
    return plan, review, {"b1": report, "source_verification": attestation}


def _query_counts(connection, version_id: int) -> dict[str, int]:
    from app.db import execute

    cycles = execute(
        connection,
        "SELECT COUNT(*) AS count FROM learning_plan_cycles WHERE plan_version_id=?",
        (version_id,),
    ).fetchone()
    tasks = execute(
        connection,
        "SELECT COUNT(*) AS count FROM learning_plan_tasks t JOIN learning_plan_cycles c ON c.id=t.plan_cycle_id WHERE c.plan_version_id=?",
        (version_id,),
    ).fetchone()
    tracks = execute(
        connection,
        "SELECT cohort_month, COUNT(*) AS count FROM learning_plan_cycles WHERE plan_version_id=? GROUP BY cohort_month ORDER BY cohort_month",
        (version_id,),
    ).fetchall()
    return {
        "version_count": int(
            execute(
                connection,
                "SELECT COUNT(*) AS count FROM learning_plan_versions WHERE plan_key='standard-3y' AND version_label='2026'",
            ).fetchone()["count"]
        ),
        "cycle_count": int(cycles["count"]),
        "task_count": int(tasks["count"]),
        "tracks": {str(row["cohort_month"]): int(row["count"]) for row in tracks},
    }


def _assert_counts(counts: dict[str, int]) -> None:
    if counts["version_count"] != 1:
        _fail(f"计划版本数不是1: {counts}")
    if counts["cycle_count"] != EXPECTED_CYCLES:
        _fail(f"周期数不是144: {counts}")
    if counts["task_count"] != EXPECTED_TASKS:
        _fail(f"任务数不是1819: {counts}")
    if counts["tracks"] != {str(month): 36 for month in EXPECTED_TRACKS}:
        _fail(f"四条轨道计数异常: {counts}")


def _api_readback(plan: dict, version_id: int) -> dict:
    """Exercise the authenticated API against the same MySQL connection."""

    from fastapi.testclient import TestClient
    from app.main import app
    from app.services.iam import seed_iam

    seed_iam()
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={
                "username": os.getenv("BOOTSTRAP_ADMIN_USERNAME", "b2-ci-admin"),
                "password": os.getenv("BOOTSTRAP_ADMIN_PASSWORD", ""),
            },
        )
        if login.status_code != 200:
            _fail(f"CI 管理员登录失败: {login.status_code} {login.text}")
        token = login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        response = client.get("/api/v1/learning-plans", headers=headers)
        if response.status_code != 200:
            _fail(f"学习计划 API 回读失败: {response.status_code} {response.text}")
        payload = response.json().get("data") or []
        matching = [item for item in payload if item.get("id") == version_id]
        if len(matching) != 1:
            _fail(f"API 未返回唯一导入版本: {payload}")
        api_plan = matching[0]
        tracks = api_plan.get("cohort_tracks") or []
        if {track.get("cohort_month") for track in tracks} != set(EXPECTED_TRACKS):
            _fail("API 四轨道不完整")
        checks = []
        for source_track in plan["cohort_tracks"]:
            cohort = source_track["cohort_month"]
            api_track = next(track for track in tracks if track.get("cohort_month") == cohort)
            if len(api_track.get("cycles") or []) != 36:
                _fail(f"API {cohort}月轨道不是36周期")
            for cycle_index in (1, 12, 13, 24, 25, 36):
                source_cycle = next(c for c in source_track["cycles"] if c["cycle_index"] == cycle_index)
                api_cycle = next(c for c in api_track["cycles"] if c.get("cycle_index") == cycle_index)
                source_tasks = source_cycle.get("tasks") or []
                api_tasks = api_cycle.get("tasks") or []
                if len(source_tasks) != len(api_tasks):
                    _fail(f"API {cohort}/{cycle_index} 任务数不一致")
                for expected, actual in zip(source_tasks, api_tasks, strict=True):
                    for field in ("task_type", "title", "description", "credit_points", "sort_order"):
                        expected_value = expected.get(field)
                        actual_value = actual.get(field)
                        if field == "credit_points" and expected_value is not None and actual_value is not None:
                            values_match = float(expected_value) == float(actual_value)
                        else:
                            values_match = expected_value == actual_value
                        if not values_match:
                            _fail(f"API {cohort}/{cycle_index} 字段不一致: {field}")
                checks.append(f"{cohort}-{cycle_index}")
        return {"status": response.status_code, "checked_boundaries": checks}


def _seed_test_classes(connection) -> list[tuple[str, str, int]]:
    from app.db import execute

    now = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    root = execute(connection, "SELECT id FROM org_units WHERE unit_code='SZ_ROOT'").fetchone()
    if not root:
        _fail("seed_iam 未创建苏州根组织")
    result = []
    for cohort in EXPECTED_TRACKS:
        class_id = f"b2-ci-class-{cohort}"
        group_id = f"b2-ci-group-{cohort}"
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) VALUES (?, ?, ?, 'CLASS', ?, 1, ?, ?)",
            (class_id, class_id.upper(), f"B2 CI {cohort}月班", root["id"], now, now),
        )
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) VALUES (?, ?, ?, 'GROUP', ?, 1, ?, ?)",
            (group_id, group_id.upper(), f"B2 CI {cohort}月组", class_id, now, now),
        )
        result.append((class_id, group_id, cohort))
    return result


def _cycle_engine(version_id: int, admin_user_id: int) -> dict:
    from app.db import transaction
    from app.services.learning_cycles import (
        bind_class_learning_plan,
        confirm_class_meeting,
        get_class_learning_progress,
        update_current_learning_cycle,
    )

    with transaction() as connection:
        classes = _seed_test_classes(connection)
    try:
        bind_class_learning_plan(
            actor_user_id=admin_user_id,
            class_org_unit_id=classes[0][0],
            plan_version_id=version_id,
            cohort_month=classes[0][2],
        )
    except ValueError as exc:
        if "只有已发布" not in str(exc):
            raise
    else:
        _fail("DRAFT 版本被错误允许绑定班级")
    with transaction() as connection:
        from app.db import execute

        execute(connection, "UPDATE learning_plan_versions SET status='PUBLISHED' WHERE id=?", (version_id,))
    results = []
    for class_id, group_id, cohort in classes:
        try:
            # Use a historical synthetic timeline so the service's "current
            # time" lookup can immediately see the next cycle after each
            # confirmation; no wall-clock sleep is needed in CI.
            timeline_start = datetime.now(UTC) - timedelta(days=365)
            bind_class_learning_plan(
                actor_user_id=admin_user_id,
                class_org_unit_id=class_id,
                plan_version_id=version_id,
                cohort_month=cohort,
                started_at=timeline_start.isoformat(),
            )
            initial = get_class_learning_progress(user_id=admin_user_id, class_org_unit_id=class_id)
            if initial["current_cycle"]["learning_cycle_index"] != 1:
                _fail(f"{cohort}月班未从周期1开始")

            # A postponed class does not advance, even if its group task is done.
            update_current_learning_cycle(
                actor_user_id=admin_user_id,
                class_org_unit_id=class_id,
                updates={
                    "class_meeting_status": "POSTPONED",
                    "group_tasks": [{"group_org_unit_id": group_id, "status": "COMPLETED"}],
                },
            )
            postponed = get_class_learning_progress(user_id=admin_user_id, class_org_unit_id=class_id)
            if postponed["current_cycle"]["learning_cycle_index"] != 1:
                _fail(f"{cohort}月班延期时错误推进")
            update_current_learning_cycle(
                actor_user_id=admin_user_id,
                class_org_unit_id=class_id,
                updates={"class_meeting_status": "PLANNED"},
            )

            first = timeline_start + timedelta(seconds=1)
            progress = confirm_class_meeting(
                actor_user_id=admin_user_id,
                class_org_unit_id=class_id,
                actual_class_meeting_at=first.isoformat(),
                confirmation_reason="B2 CI 周期边界验证",
            )
            if progress["current_cycle"]["learning_cycle_index"] != 2:
                from app.db import fetch_all

                rows = fetch_all(
                    "SELECT learning_cycle_index, opened_at, actual_class_meeting_at, cycle_status "
                    "FROM class_learning_cycles WHERE class_org_unit_id=? ORDER BY learning_cycle_index",
                    (class_id,),
                )
                _fail(f"{cohort}月班班会确认后未进入周期2: {rows}")
            # A group completed after the class meeting is stored on cycle 2.
            update_current_learning_cycle(
                actor_user_id=admin_user_id,
                class_org_unit_id=class_id,
                updates={"group_tasks": [{"group_org_unit_id": group_id, "status": "COMPLETED"}]},
            )
            next_progress = get_class_learning_progress(user_id=admin_user_id, class_org_unit_id=class_id)
            if next_progress["current_cycle"]["learning_cycle_index"] != 2:
                _fail(f"{cohort}月班会后小组会未归入周期2")

            current_meeting = first
            for cycle_index in range(2, 37):
                update_current_learning_cycle(
                    actor_user_id=admin_user_id,
                    class_org_unit_id=class_id,
                    updates={"group_meeting_policy": "SUSPENDED"},
                )
                current_meeting = current_meeting + timedelta(seconds=1)
                progress = confirm_class_meeting(
                    actor_user_id=admin_user_id,
                    class_org_unit_id=class_id,
                    actual_class_meeting_at=current_meeting.isoformat(),
                    confirmation_reason="B2 CI 周期边界验证",
                )
                expected = cycle_index + 1 if cycle_index < 36 else 36
                if cycle_index < 36 and progress["current_cycle"]["learning_cycle_index"] != expected:
                    _fail(f"{cohort}月班未正确推进 {cycle_index}->{expected}")
            final = get_class_learning_progress(user_id=admin_user_id, class_org_unit_id=class_id)
            if final["binding"]["status"] != "COMPLETED":
                _fail(f"{cohort}月班周期36完成后绑定未完成")
            results.append({"cohort_month": cohort, "cycle_1_to_36": "PASS", "binding_status": final["binding"]["status"]})
        except Exception:
            # Keep failures useful while allowing the workflow to fail normally.
            raise
    return {"classes": results, "tracks_checked": list(EXPECTED_TRACKS)}


def run(apply: bool) -> dict:
    target = _assert_ci_target()
    plan, review, source = _load_source_attestation()
    if not apply:
        return {"target": target, "source": source, "applied": False}

    from app.db import execute, transaction
    from app.migrations import run_migrations
    from app.services.iam import seed_iam

    migrations = run_migrations()
    seed_iam()
    with transaction() as connection:
        first = import_plan(connection, plan, actor_user_id=None)
        first_counts = _query_counts(connection, first["version_id"])
    _assert_counts(first_counts)

    with transaction() as connection:
        second = import_plan(connection, plan, actor_user_id=None)
        second_counts = _query_counts(connection, second["version_id"])
    _assert_counts(second_counts)
    if second["version_id"] != first["version_id"] or second_counts != first_counts:
        _fail("第二次导入没有保持幂等")

    api = _api_readback(plan, first["version_id"])
    with transaction() as connection:
        # Draft must reject class binding before the controlled CI publish.
        user = execute(
            connection,
            "SELECT id FROM app_users WHERE username=?",
            (os.getenv("BOOTSTRAP_ADMIN_USERNAME", "b2-ci-admin"),),
        ).fetchone()
        if not user:
            _fail("CI 管理员不存在")
        admin_user_id = int(user["id"])
        draft_status = execute(
            connection,
            "SELECT status FROM learning_plan_versions WHERE id=?",
            (first["version_id"],),
        ).fetchone()["status"]
        if draft_status != "DRAFT":
            _fail(f"首次导入后计划不是 DRAFT: {draft_status}")

    cycle = _cycle_engine(first["version_id"], admin_user_id)
    with transaction() as connection:
        published = execute(
            connection,
            "SELECT status FROM learning_plan_versions WHERE id=?",
            (first["version_id"],),
        ).fetchone()["status"]
        if published != "PUBLISHED":
            _fail("CI 受控发布后状态不是 PUBLISHED")
        try:
            import_plan(connection, plan, actor_user_id=None)
        except ValueError as exc:
            if "禁止覆盖" not in str(exc):
                raise
            overwrite = {"status": "REJECTED", "message": str(exc)}
        else:
            _fail("PUBLISHED 版本被错误覆盖")
        protected_rows = execute(
            connection,
            "SELECT COUNT(*) AS count FROM learning_plan_versions WHERE status='PUBLISHED'",
        ).fetchone()["count"]
        score_rows = execute(
            connection,
            "SELECT COUNT(*) AS count FROM attendance_score_records",
        ).fetchone()["count"]
        credit_rows = execute(
            connection,
            "SELECT COUNT(*) AS count FROM learning_plan_credit_rule_versions",
        ).fetchone()["count"]

    return {
        "target": target,
        "source": source,
        "review_status": review["status"],
        "migrations_applied": migrations,
        "first_import": first,
        "first_counts": first_counts,
        "second_import": second,
        "second_counts": second_counts,
        "api_readback": api,
        "cycle_engine": cycle,
        "publish": {"status": "PUBLISHED", "published_version_count": int(protected_rows)},
        "published_overwrite": overwrite,
        "learner_score_rows": int(score_rows),
        "credit_rule_version_rows": int(credit_rows),
        "production_write": False,
        "identity_write": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="CI 临时 MySQL 上执行 B2 学习计划验证")
    parser.add_argument("--apply", action="store_true", help="显式写入本次临时 MySQL 数据库")
    parser.add_argument("--report", type=Path, default=None, help="将 JSON 结果写入指定文件")
    args = parser.parse_args()
    result = run(apply=args.apply)
    serialized = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(serialized)
    if args.report:
        args.report.write_text(serialized + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
