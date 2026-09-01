from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


SCRIPTS_ROOT = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from learning_plan_2026 import (  # noqa: E402
    COHORT_MONTHS,
    PlanValidationError,
    _course_fragments,
    _tasks_for_cell,
    import_plan,
    validate_plan,
)
from review_learning_plan_2026 import (  # noqa: E402
    build_review_manifest,
    derive_manifest_status,
    verify_review_manifest,
)


def _synthetic_plan() -> dict:
    tracks = []
    for cohort_month in COHORT_MONTHS:
        cycles = []
        for cycle_index in range(1, 37):
            year_index = ((cycle_index - 1) // 12) + 1
            year_cycle_index = ((cycle_index - 1) % 12) + 1
            metadata = {
                "source_file": "fixture.xlsx",
                "source_sheet": f"{cohort_month}月开班",
                "source_cell": f"B{cycle_index + 3}",
                "source_year": year_index,
                "source_cohort_month": cohort_month,
                "nominal_calendar_month": cohort_month,
                "source_section": "小组学习会",
                "source_text": "半天",
            }
            cycles.append(
                {
                    "cycle_index": cycle_index,
                    "year_index": year_index,
                    "year_cycle_index": year_cycle_index,
                    "nominal_calendar_month": cohort_month,
                    "label": f"第{year_index}学年第{year_cycle_index}学习周期",
                    "tasks": [
                        {
                            "task_type": "GROUP_MEETING",
                            "title": "小组学习会",
                            "description": "半天",
                            "credit_points": None,
                            "canonical_key": None,
                            "is_required": True,
                            "sort_order": 1,
                            "metadata": metadata,
                        }
                    ],
                }
            )
        tracks.append({"cohort_month": cohort_month, "cycles": cycles})
    return {
        "schema_version": 1,
        "plan_key": "standard-3y",
        "plan_name": "标准班三年学习计划",
        "version_label": "2026",
        "duration_cycles": 36,
        "status": "DRAFT",
        "source": {"formal_sheets": {"fixture": True}},
        "cohort_tracks": tracks,
    }


def test_online_course_cell_splits_and_keeps_credit_unknown() -> None:
    tasks = _tasks_for_cell(
        section="线上课程",
        raw="【课程A】（30分钟）\n【幸福测评表讲解】（21分钟）",
        source_file="fixture.xlsx",
        sheet="26版1月开班",
        row=10,
        col=2,
        source_year=1,
        cohort_month=1,
        nominal_calendar_month=1,
    )
    assert [task["task_type"] for task in tasks] == ["ONLINE_COURSE", "ONLINE_COURSE"]
    assert tasks[0]["title"] == "课程A"
    assert tasks[0]["credit_points"] is None
    assert tasks[1]["credit_points"] == 20
    assert tasks[1]["metadata"]["duration_minutes"] == 21
    assert tasks[1]["metadata"]["source_cell"] == "B10"


def test_component_row_is_not_multiple_group_meetings() -> None:
    tasks = _tasks_for_cell(
        section="小组学习会\n学员企业走访",
        raw="企业走访+幸福关爱+空巴",
        source_file="fixture.xlsx",
        sheet="26版1月开班",
        row=17,
        col=2,
        source_year=1,
        cohort_month=1,
        nominal_calendar_month=1,
    )
    assert {task["task_type"] for task in tasks} == {
        "ENTERPRISE_VISIT",
        "CARE",
        "KONPA",
    }
    assert all(task["metadata"]["source_cell"] == "B17" for task in tasks)


def test_plan_quality_gate_requires_144_cycles_and_group_tasks() -> None:
    plan = _synthetic_plan()
    report = validate_plan(plan)
    assert report["cycle_count"] == 144
    assert report["cycles_per_track"] == {"1": 36, "4": 36, "7": 36, "10": 36}
    assert report["missing_group_meeting_cycles"] == []
    assert report["errors"] == []

    broken = json.loads(json.dumps(plan))
    broken["cohort_tracks"][0]["cycles"].pop()
    with pytest.raises(PlanValidationError):
        from learning_plan_2026 import assert_valid_plan

        assert_valid_plan(broken)


def test_draft_import_is_idempotent_and_published_is_protected() -> None:
    from app.db import execute, transaction

    plan = _synthetic_plan()
    with transaction() as connection:
        existing = execute(
            connection,
            "SELECT id, status FROM learning_plan_versions WHERE plan_key=? AND version_label=?",
            (plan["plan_key"], plan["version_label"]),
        ).fetchone()
        if existing and existing["status"] == "DRAFT":
            execute(
                connection,
                "DELETE FROM learning_plan_tasks WHERE plan_cycle_id IN "
                "(SELECT id FROM learning_plan_cycles WHERE plan_version_id=?)",
                (existing["id"],),
            )
            execute(connection, "DELETE FROM learning_plan_cycles WHERE plan_version_id=?", (existing["id"],))
            execute(connection, "DELETE FROM learning_plan_versions WHERE id=?", (existing["id"],))
        first = import_plan(connection, plan, actor_user_id=None)
    assert first["cycle_count"] == 144

    with transaction() as connection:
        second = import_plan(connection, plan, actor_user_id=None)
        counts = execute(
            connection,
            "SELECT COUNT(*) AS cycles FROM learning_plan_cycles WHERE plan_version_id=?",
            (second["version_id"],),
        ).fetchone()
        assert int(counts["cycles"]) == 144
        execute(
            connection,
            "UPDATE learning_plan_versions SET status='PUBLISHED' WHERE id=?",
            (second["version_id"],),
        )
        with pytest.raises(ValueError, match="禁止覆盖"):
            import_plan(connection, plan)


def test_course_fragment_number_suffixes_are_split() -> None:
    assert _course_fragments("【哲学手册编制】1、2") == [
        "哲学手册编制1",
        "哲学手册编制2",
    ]


def test_review_manifest_binds_json_hash_and_all_36_checkpoints(tmp_path: Path) -> None:
    plan = _synthetic_plan()
    source_json = tmp_path / "standard-3y-2026.json"
    source_json.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    manifest = build_review_manifest(plan, source_commit="370dc94", source_json=source_json)
    assert manifest["status"] == "PENDING"
    assert manifest["required_checkpoint_count"] == 36
    checkpoint = next(
        item
        for item in manifest["checkpoints"]
        if item["cohort_month"] == 1 and item["learning_cycle_index"] == 1
    )
    assert checkpoint["template_label"] == "1月开班模板"
    assert checkpoint["checkpoint_label"] == "cohort_month=1, learning_cycle_index=1"
    assert checkpoint["learning_cycle_label"] == "1月开班模板 · 第1学习周期"
    verify_review_manifest(manifest, plan=plan, source_json=source_json, expected_source_commit="370dc94")

    confirmed = json.loads(json.dumps(manifest))
    confirmed["status"] = "CONFIRMED"
    confirmed["confirmed_by"] = "reviewer"
    confirmed["confirmed_at"] = "2026-08-25T00:00:00+08:00"
    for checkpoint in confirmed["checkpoints"]:
        checkpoint["status"] = "CONFIRMED"
        checkpoint["reviewed_by"] = "reviewer"
        checkpoint["reviewed_at"] = "2026-08-25T00:00:00+08:00"
    verify_review_manifest(
        confirmed,
        plan=plan,
        source_json=source_json,
        expected_source_commit="370dc94",
        require_confirmed=True,
    )

    source_json.write_text(source_json.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        verify_review_manifest(confirmed, plan=plan, source_json=source_json, expected_source_commit="370dc94")


def test_review_status_is_derived_from_each_checkpoint(tmp_path: Path) -> None:
    plan = _synthetic_plan()
    # The fixture uses a temporary JSON below so the test is independent of the
    # checked-in production-sized source file.
    source_json = tmp_path / "_review-status-fixture.json"
    source_json.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    manifest = build_review_manifest(plan, source_commit="370dc94", source_json=source_json)
    assert derive_manifest_status(manifest) == "PENDING"
    manifest["status"] = "CONFIRMED"
    with pytest.raises(ValueError, match="自动派生"):
        verify_review_manifest(manifest, plan=plan, source_json=source_json, expected_source_commit="370dc94")


def test_review_manifest_checks_all_three_workbook_fingerprints(tmp_path: Path) -> None:
    plan = _synthetic_plan()
    source_json = tmp_path / "standard-3y-2026.json"
    source_json.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    workbooks = {}
    for year in (1, 2, 3):
        path = tmp_path / f"year{year}.xlsx"
        path.write_bytes(f"workbook-{year}".encode("utf-8"))
        workbooks[year] = path
    manifest = build_review_manifest(
        plan,
        source_commit="370dc94",
        source_json=source_json,
        source_workbooks=workbooks,
    )
    verify_review_manifest(
        manifest,
        plan=plan,
        source_json=source_json,
        expected_source_commit="370dc94",
        source_workbooks=workbooks,
    )
    workbooks[2].write_bytes(b"changed")
    with pytest.raises(ValueError, match="第2年原始 Excel SHA-256"):
        verify_review_manifest(
            manifest,
            plan=plan,
            source_json=source_json,
            expected_source_commit="370dc94",
            source_workbooks=workbooks,
        )
