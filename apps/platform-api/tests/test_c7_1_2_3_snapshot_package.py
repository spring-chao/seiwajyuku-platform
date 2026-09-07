from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from validate_c7_authoritative_snapshot import validate_snapshot


WINDOW_START = "2026-08-31"
WINDOW_END = "2026-09-04"


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _create_snapshot(tmp_path: Path) -> tuple[Path, Path]:
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()
    groups = {"1组": "group-1", "2组": "group-2", "3组": "group-3", "4组": "group-4", "5组": "group-5", "精进组": "group-advanced"}
    tables: dict[str, tuple[list[str], list[dict[str, str]]]] = {
        "class_candidates": (
            ["id", "name", "unit_type", "parent_id", "active_from", "active_until", "is_active"],
            [{"id": "class-1", "name": "吴越三班", "unit_type": "CLASS", "parent_id": "center-1", "active_from": "2026-07-01", "active_until": "", "is_active": "1"}],
        ),
        "org_units": (
            ["id", "name", "unit_type", "parent_id", "active_from", "active_until", "is_active"],
            [
                {"id": "center-1", "name": "测试分中心", "unit_type": "REGIONAL_CENTER", "parent_id": "root", "active_from": "2020-01-01", "active_until": "", "is_active": "1"},
                {"id": "class-1", "name": "吴越三班", "unit_type": "CLASS", "parent_id": "center-1", "active_from": "2026-07-01", "active_until": "", "is_active": "1"},
                *[
                    {"id": group_id, "name": f"吴越三班{label}", "unit_type": "GROUP", "parent_id": "class-1", "active_from": "2026-07-01", "active_until": "", "is_active": "1"}
                    for label, group_id in groups.items()
                ],
            ],
        ),
        "members": (
            ["id", "name", "member_code", "status"],
            [
                {"id": "100", "name": "测试学员一", "member_code": "M100", "status": "ACTIVE"},
                {"id": "101", "name": "测试学员二", "member_code": "M101", "status": "ACTIVE"},
            ],
        ),
        "member_org_relations": (
            ["id", "member_id", "org_unit_id", "relation_type", "is_primary", "valid_from", "valid_until", "source_type"],
            [
                {"id": "1", "member_id": "100", "org_unit_id": "class-1", "relation_type": "STUDY_CLASS", "is_primary": "1", "valid_from": "2026-07-01", "valid_until": "", "source_type": "TEST"},
                {"id": "2", "member_id": "100", "org_unit_id": "group-1", "relation_type": "STUDY_GROUP", "is_primary": "1", "valid_from": "2026-07-01", "valid_until": "", "source_type": "TEST"},
                {"id": "3", "member_id": "101", "org_unit_id": "class-1", "relation_type": "STUDY_CLASS", "is_primary": "1", "valid_from": "2026-07-01", "valid_until": "", "source_type": "TEST"},
                {"id": "4", "member_id": "101", "org_unit_id": "group-advanced", "relation_type": "STUDY_GROUP", "is_primary": "1", "valid_from": "2026-07-01", "valid_until": "", "source_type": "TEST"},
            ],
        ),
        "class_learning_bindings": (
            ["id", "class_org_unit_id", "plan_version_id", "cohort_month", "started_at", "ended_at", "status", "learning_round", "transition_type", "credit_rule_version_id", "course_credit_rule_version_id"],
            [{"id": "10", "class_org_unit_id": "class-1", "plan_version_id": "20", "cohort_month": "7", "started_at": "2026-07-01T00:00:00+08:00", "ended_at": "", "status": "ACTIVE", "learning_round": "1", "transition_type": "INITIAL", "credit_rule_version_id": "30", "course_credit_rule_version_id": "40"}],
        ),
        "class_learning_cycles": (
            ["id", "binding_id", "class_org_unit_id", "learning_cycle_index", "plan_cycle_id", "opened_at", "actual_class_meeting_at", "cycle_status", "closed_at"],
            [{"id": "11", "binding_id": "10", "class_org_unit_id": "class-1", "learning_cycle_index": "3", "plan_cycle_id": "21", "opened_at": "2026-08-01T00:00:00+08:00", "actual_class_meeting_at": "", "cycle_status": "OPEN", "closed_at": "2026-09-30T00:00:00+08:00"}],
        ),
        "learning_plan_versions": (
            ["id", "plan_key", "plan_name", "version_label", "duration_cycles", "status"],
            [{"id": "20", "plan_key": "standard-3y", "plan_name": "标准三年", "version_label": "2026", "duration_cycles": "36", "status": "PUBLISHED"}],
        ),
        "schema_migrations": (
            ["version", "applied_at"],
            [{"version": "0050", "applied_at": "2026-09-01T00:00:00+08:00"}],
        ),
    }
    files: dict[str, dict[str, object]] = {}
    for table_name, (headers, rows) in tables.items():
        filename = f"{table_name}.csv"
        path = snapshot_dir / filename
        _write_csv(path, headers, rows)
        files[table_name] = {"path": filename, "row_count": len(rows), "sha256": _sha256(path)}
    manifest = {
        "schema_version": "C7.1.2.3-READONLY-SNAPSHOT-V1",
        "source": {"environment": "production", "extraction_mode": "READ_ONLY_CONSISTENT_SNAPSHOT", "attestation": {"status": "APPROVED", "reference": "DBA-TEST-001"}},
        "scope": {"class_name": "吴越三班", "class_org_unit_id": "class-1", "window_start": WINDOW_START, "window_end": WINDOW_END},
        "source_group_mappings": groups,
        "files": files,
    }
    manifest_path = snapshot_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (snapshot_dir / "manifest.sha256").write_text(f"{_sha256(manifest_path)}  manifest.json\n", encoding="utf-8")
    scope_path = snapshot_dir / "credit-scope.json"
    scope_path.write_text(json.dumps([{"member_id": "100", "expected_group_org_unit_id": "group-1"}, {"member_id": "101", "expected_group_org_unit_id": "group-advanced"}], ensure_ascii=False), encoding="utf-8")
    return snapshot_dir, scope_path


def test_snapshot_package_preflight_passes_without_claiming_identity_reconciliation(tmp_path: Path) -> None:
    snapshot_dir, _ = _create_snapshot(tmp_path)

    report = validate_snapshot(snapshot_dir)

    assert report["snapshot_preflight_status"] == "PASS"
    assert report["gates"]["PLATFORM_SNAPSHOT_INTEGRITY"]["status"] == "PASS"
    assert report["gates"]["ORG_HISTORICAL_COVERAGE"]["status"] == "PASS"
    assert report["gates"]["MEMBER_RELATION_COVERAGE"]["status"] == "NOT_RUN"
    assert report["gates"]["REAL_FACT_RECONCILIATION"]["status"].startswith("BLOCKED")
    assert report["safety"] == {
        "database_connections": 0,
        "production_queries": 0,
        "ledger_writes": 0,
        "source_fact_writes": 0,
    }


def test_snapshot_package_checks_credit_scope_historical_class_and_group_relations(tmp_path: Path) -> None:
    snapshot_dir, scope_path = _create_snapshot(tmp_path)

    report = validate_snapshot(snapshot_dir, credit_scope_path=scope_path)

    assert report["snapshot_preflight_status"] == "PASS"
    assert report["gates"]["MEMBER_RELATION_COVERAGE"]["status"] == "PASS"


def test_snapshot_package_blocks_duplicate_historical_class_candidate(tmp_path: Path) -> None:
    snapshot_dir, _ = _create_snapshot(tmp_path)
    candidates_path = snapshot_dir / "class_candidates.csv"
    with candidates_path.open("a", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(["class-duplicate", "吴越三班", "CLASS", "center-1", "2026-01-01", "", "1"])
    manifest_path = snapshot_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["class_candidates"]["row_count"] = 2
    manifest["files"]["class_candidates"]["sha256"] = _sha256(candidates_path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (snapshot_dir / "manifest.sha256").write_text(f"{_sha256(manifest_path)}  manifest.json\n", encoding="utf-8")

    report = validate_snapshot(snapshot_dir)

    assert report["snapshot_preflight_status"] == "BLOCKED"
    assert report["gates"]["ORG_HISTORICAL_COVERAGE"]["status"] == "BLOCKED"


def test_snapshot_package_blocks_missing_historical_group_relation_for_credit_scope(tmp_path: Path) -> None:
    snapshot_dir, scope_path = _create_snapshot(tmp_path)
    relation_path = snapshot_dir / "member_org_relations.csv"
    rows = list(csv.DictReader(relation_path.open("r", encoding="utf-8", newline="")))
    _write_csv(relation_path, list(rows[0]), [row for row in rows if row["id"] != "2"])
    manifest_path = snapshot_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["member_org_relations"]["row_count"] = 3
    manifest["files"]["member_org_relations"]["sha256"] = _sha256(relation_path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (snapshot_dir / "manifest.sha256").write_text(f"{_sha256(manifest_path)}  manifest.json\n", encoding="utf-8")

    report = validate_snapshot(snapshot_dir, credit_scope_path=scope_path)

    assert report["gates"]["MEMBER_RELATION_COVERAGE"]["status"] == "BLOCKED"
    assert report["snapshot_preflight_status"] == "BLOCKED"
