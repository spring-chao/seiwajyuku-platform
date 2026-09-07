#!/usr/bin/env python3
"""Validate the private, read-only C7.1.2.3 platform snapshot package.

This utility deliberately has no database driver and never opens a network
connection.  It only reads a locally supplied CSV/JSON snapshot package and
emits a structural preflight report for the C7 吴越三班 reconciliation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path, PurePosixPath
from typing import Any


DEFAULT_CLASS_NAME = "吴越三班"
DEFAULT_WINDOW_START = "2026-08-31"
DEFAULT_WINDOW_END = "2026-09-04"
EXPECTED_SOURCE_GROUPS = ("1组", "2组", "3组", "4组", "5组", "精进组")

REQUIRED_TABLES: dict[str, set[str]] = {
    "class_candidates": {
        "id",
        "name",
        "unit_type",
        "parent_id",
        "active_from",
        "active_until",
        "is_active",
    },
    "org_units": {
        "id",
        "name",
        "unit_type",
        "parent_id",
        "active_from",
        "active_until",
        "is_active",
    },
    "members": {"id", "name", "member_code", "status"},
    "member_org_relations": {
        "id",
        "member_id",
        "org_unit_id",
        "relation_type",
        "is_primary",
        "valid_from",
        "valid_until",
        "source_type",
    },
    "class_learning_bindings": {
        "id",
        "class_org_unit_id",
        "plan_version_id",
        "cohort_month",
        "started_at",
        "ended_at",
        "status",
        "learning_round",
        "transition_type",
        "credit_rule_version_id",
        "course_credit_rule_version_id",
    },
    "class_learning_cycles": {
        "id",
        "binding_id",
        "class_org_unit_id",
        "learning_cycle_index",
        "plan_cycle_id",
        "opened_at",
        "actual_class_meeting_at",
        "cycle_status",
        "closed_at",
    },
    "learning_plan_versions": {
        "id",
        "plan_key",
        "plan_name",
        "version_label",
        "duration_cycles",
        "status",
    },
    "schema_migrations": {"version", "applied_at"},
}

OPTIONAL_TABLES: dict[str, set[str]] = {
    "learning_credit_rule_versions": {"id", "rule_set_key", "version_label", "status"},
    "learning_credit_rules": {
        "id",
        "rule_version_id",
        "rule_key",
        "credit_category",
        "credit_type",
        "settlement_model",
        "status",
    },
    "learning_business_calendar_versions": {
        "id",
        "calendar_key",
        "calendar_year",
        "version_label",
        "timezone",
        "status",
    },
    "learning_business_calendar_days": {
        "calendar_version_id",
        "business_date",
        "day_type",
        "note",
    },
}

OPTIONAL_MEMBER_COLUMNS = {"phone_masked", "phone_last4"}
FORBIDDEN_COLUMNS = {
    "phone_ciphertext",
    "phone_hash",
    "phone",
    "mobile",
    "id_card",
    "id_card_number",
    "care_note",
    "care_notes",
    "followup_note",
    "followup_notes",
    "enterprise_financial_ciphertext",
    "password_hash",
    "token_hash",
}
FORBIDDEN_MANIFEST_KEY_PARTS = {
    "password",
    "secret",
    "token",
    "connectionstring",
    "connectionurl",
    "databaseurl",
    "dsn",
}
VALID_DAY_TYPES = {"NORMAL_WORKDAY", "WEEKEND", "HOLIDAY", "ADJUSTED_WORKDAY"}


class SnapshotValidationError(ValueError):
    """Raised for malformed, local snapshot input."""


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _parse_date(value: Any, field: str) -> date | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError as exc:
        raise SnapshotValidationError(f"{field} 不是有效日期: {raw}") from exc


def _window_days(start: date, end: date) -> list[date]:
    if start > end:
        raise SnapshotValidationError("window_start 不能晚于 window_end")
    result: list[date] = []
    current = start
    while current <= end:
        result.append(current)
        current += timedelta(days=1)
    return result


def _covers(row: dict[str, str], occurred_on: date, start_field: str, end_field: str) -> bool:
    start = _parse_date(row.get(start_field), start_field)
    end = _parse_date(row.get(end_field), end_field)
    return (start is None or start <= occurred_on) and (end is None or end >= occurred_on)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative_path(value: Any) -> Path:
    raw = _text(value).replace("\\", "/")
    candidate = PurePosixPath(raw)
    if not raw or candidate.is_absolute() or ".." in candidate.parts:
        raise SnapshotValidationError("manifest 文件路径必须是包内相对路径")
    return Path(*candidate.parts)


def _read_csv(path: Path) -> tuple[list[dict[str, str]], set[str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise SnapshotValidationError(f"{path.name} 缺少 CSV 表头")
            headers = {_text(name) for name in reader.fieldnames if _text(name)}
            rows = [{_text(key): _text(value) for key, value in row.items()} for row in reader]
    except UnicodeDecodeError as exc:
        raise SnapshotValidationError(f"{path.name} 必须是 UTF-8 CSV") from exc
    return rows, headers


def _gate(status: str, *reasons: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    output: dict[str, Any] = {"status": status, "reasons": list(reasons)}
    if evidence:
        output["evidence"] = evidence
    return output


def _load_manifest(snapshot_dir: Path) -> tuple[dict[str, Any] | None, list[str]]:
    manifest_path = snapshot_dir / "manifest.json"
    errors: list[str] = []
    if not manifest_path.is_file():
        return None, ["manifest.json 缺失"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"manifest.json 无法解析: {exc}"]
    if not isinstance(manifest, dict):
        errors.append("manifest.json 顶层必须是对象")
    sidecar = snapshot_dir / "manifest.sha256"
    if not sidecar.is_file():
        errors.append("manifest.sha256 缺失；清单自身必须使用独立 SHA-256 sidecar")
    else:
        parts = sidecar.read_text(encoding="utf-8").split(maxsplit=1)
        declared = _text(parts[0] if parts else "").lower()
        if len(declared) != 64 or declared != _sha256(manifest_path):
            errors.append("manifest.sha256 与 manifest.json 不一致")
    return manifest if isinstance(manifest, dict) else None, errors


def _manifest_has_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            compact_key = "".join(char for char in _text(key).lower() if char.isalnum())
            if any(part in compact_key for part in FORBIDDEN_MANIFEST_KEY_PARTS):
                return True
            if _manifest_has_forbidden_key(child):
                return True
    elif isinstance(value, list):
        return any(_manifest_has_forbidden_key(item) for item in value)
    return False


def _load_credit_scope(path: Path | None) -> tuple[list[dict[str, str]] | None, list[str]]:
    if path is None:
        return None, []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"credit scope 文件无法解析: {exc}"]
    if not isinstance(raw, list):
        return None, ["credit scope 文件必须是 JSON 数组"]
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict) or not _text(item.get("member_id")):
            return None, [f"credit scope 第 {index} 行缺少 member_id"]
        member_id = _text(item["member_id"])
        if member_id in seen:
            return None, [f"credit scope 出现重复 member_id: {member_id}"]
        seen.add(member_id)
        entries.append(
            {
                "member_id": member_id,
                "expected_group_org_unit_id": _text(item.get("expected_group_org_unit_id")),
            }
        )
    return entries, []


def _normalize_group_mapping(manifest: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    mapping = manifest.get("source_group_mappings")
    if not isinstance(mapping, dict):
        return {}, ["manifest.source_group_mappings 必须是对象"]
    output = {_text(key): _text(value) for key, value in mapping.items()}
    missing = [key for key in EXPECTED_SOURCE_GROUPS if not output.get(key)]
    extra = sorted(set(output) - set(EXPECTED_SOURCE_GROUPS))
    errors: list[str] = []
    if missing:
        errors.append("source_group_mappings 缺少: " + ", ".join(missing))
    if extra:
        errors.append("source_group_mappings 包含未授权来源小组: " + ", ".join(extra))
    if len(set(output.values())) != len(output.values()):
        errors.append("source_group_mappings 不能将多个来源小组映射到同一组织节点")
    return output, errors


def validate_snapshot(
    snapshot_dir: Path,
    *,
    class_name: str = DEFAULT_CLASS_NAME,
    window_start: str = DEFAULT_WINDOW_START,
    window_end: str = DEFAULT_WINDOW_END,
    credit_scope_path: Path | None = None,
) -> dict[str, Any]:
    """Validate a private snapshot package without querying any database."""

    snapshot_dir = snapshot_dir.resolve()
    start = _parse_date(window_start, "window_start")
    end = _parse_date(window_end, "window_end")
    assert start is not None and end is not None
    days = _window_days(start, end)
    manifest, errors = _load_manifest(snapshot_dir)
    credit_scope, scope_errors = _load_credit_scope(credit_scope_path)
    errors.extend(scope_errors)
    gates: dict[str, dict[str, Any]] = {}
    tables: dict[str, list[dict[str, str]]] = {}

    if manifest is None:
        gates["PLATFORM_SNAPSHOT_SOURCE"] = _gate("BLOCKED", "SNAPSHOT_MANIFEST_MISSING")
        gates["PLATFORM_SNAPSHOT_INTEGRITY"] = _gate("BLOCKED", *errors)
        return _final_report(snapshot_dir, class_name, start, end, gates, errors)

    scope = manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {}
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    attestation = source.get("attestation") if isinstance(source.get("attestation"), dict) else {}
    source_reasons: list[str] = []
    if _text(manifest.get("schema_version")) != "C7.1.2.3-READONLY-SNAPSHOT-V1":
        source_reasons.append("manifest.schema_version 必须为 C7.1.2.3-READONLY-SNAPSHOT-V1")
    if _manifest_has_forbidden_key(manifest):
        source_reasons.append("manifest 不得包含密码、密钥、令牌、连接串或 DSN 字段")
    if _text(scope.get("class_name")) != class_name:
        source_reasons.append("manifest scope.class_name 与请求目标不一致")
    if _text(scope.get("window_start")) != start.isoformat() or _text(scope.get("window_end")) != end.isoformat():
        source_reasons.append("manifest 时间窗口与请求目标不一致")
    if not _text(scope.get("class_org_unit_id")):
        source_reasons.append("manifest scope.class_org_unit_id 缺失")
    if _text(source.get("extraction_mode")) != "READ_ONLY_CONSISTENT_SNAPSHOT":
        source_reasons.append("source.extraction_mode 必须为 READ_ONLY_CONSISTENT_SNAPSHOT")
    if _text(attestation.get("status")) != "APPROVED":
        source_reasons.append("需要数据所有者的 READ_ONLY 快照批准证明")
    if not _text(attestation.get("reference")):
        source_reasons.append("source.attestation.reference 缺失")
    gates["PLATFORM_SNAPSHOT_SOURCE"] = (
        _gate("PASS", evidence={"extraction_mode": source.get("extraction_mode")})
        if not source_reasons
        else _gate("BLOCKED", *source_reasons)
    )

    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else None
    if files is None:
        errors.append("manifest.files 必须是对象")
        files = {}

    all_specs = {**REQUIRED_TABLES, **OPTIONAL_TABLES}
    for table_name, required_headers in all_specs.items():
        spec = files.get(table_name)
        if spec is None:
            if table_name in REQUIRED_TABLES:
                errors.append(f"manifest.files.{table_name} 缺失")
            continue
        if not isinstance(spec, dict):
            errors.append(f"manifest.files.{table_name} 必须是对象")
            continue
        try:
            relative_path = _safe_relative_path(spec.get("path"))
        except SnapshotValidationError as exc:
            errors.append(f"{table_name}: {exc}")
            continue
        path = snapshot_dir / relative_path
        if not path.is_file():
            errors.append(f"{table_name}: 文件不存在 ({relative_path.as_posix()})")
            continue
        expected_sha = _text(spec.get("sha256")).lower()
        if len(expected_sha) != 64 or expected_sha != _sha256(path):
            errors.append(f"{table_name}: SHA-256 不一致")
        try:
            rows, headers = _read_csv(path)
        except SnapshotValidationError as exc:
            errors.append(f"{table_name}: {exc}")
            continue
        expected_row_count = spec.get("row_count")
        if not isinstance(expected_row_count, int) or expected_row_count != len(rows):
            errors.append(f"{table_name}: row_count 与实际 CSV 行数不一致")
        allowed_headers = set(required_headers)
        if table_name == "members":
            allowed_headers |= OPTIONAL_MEMBER_COLUMNS
        missing = sorted(required_headers - headers)
        unexpected = sorted(headers - allowed_headers)
        forbidden = sorted(headers & FORBIDDEN_COLUMNS)
        if missing:
            errors.append(f"{table_name}: 缺少字段 {', '.join(missing)}")
        if unexpected:
            errors.append(f"{table_name}: 出现未在白名单中的字段 {', '.join(unexpected)}")
        if forbidden:
            errors.append(f"{table_name}: 包含禁止敏感字段 {', '.join(forbidden)}")
        tables[table_name] = rows

    integrity_status = "PASS" if not errors else "BLOCKED"
    gates["PLATFORM_SNAPSHOT_INTEGRITY"] = _gate(
        integrity_status,
        *errors,
        evidence={"validated_tables": sorted(tables), "window_days": len(days)},
    )
    if errors:
        return _final_report(snapshot_dir, class_name, start, end, gates, errors)

    group_mapping, group_mapping_errors = _normalize_group_mapping(manifest)
    class_id = _text(scope.get("class_org_unit_id"))
    org_by_id = {_text(row["id"]): row for row in tables["org_units"]}
    candidate_class_rows = [
        row
        for row in tables["class_candidates"]
        if _text(row.get("name")) == class_name
        and _text(row.get("unit_type")) == "CLASS"
        and all(_covers(row, item, "active_from", "active_until") for item in days)
    ]
    class_rows = [
        row
        for row in tables["org_units"]
        if _text(row.get("id")) == class_id
        and _text(row.get("name")) == class_name
        and _text(row.get("unit_type")) == "CLASS"
        and all(_covers(row, item, "active_from", "active_until") for item in days)
    ]
    org_reasons = list(group_mapping_errors)
    if len(candidate_class_rows) != 1:
        org_reasons.append("目标班级名称候选必须唯一且在全部事实日期有效")
    elif _text(candidate_class_rows[0].get("id")) != class_id:
        org_reasons.append("manifest class_org_unit_id 与候选班级预检结果不一致")
    if len(class_rows) != 1:
        org_reasons.append("目标班级必须在 org_units 导出中唯一且在全部事实日期有效")
    elif not _text(class_rows[0].get("parent_id")) or _text(class_rows[0]["parent_id"]) not in org_by_id:
        org_reasons.append("目标班级的直属父组织缺失，无法核验组织树")
    for source_group, group_id in group_mapping.items():
        group = org_by_id.get(group_id)
        if group is None:
            org_reasons.append(f"{source_group} 映射的组织节点不存在")
        elif (
            _text(group.get("unit_type")) != "GROUP"
            or _text(group.get("parent_id")) != class_id
            or not all(_covers(group, item, "active_from", "active_until") for item in days)
        ):
            org_reasons.append(f"{source_group} 不是目标班级窗口内有效的直属 GROUP")
    gates["ORG_HISTORICAL_COVERAGE"] = (
        _gate("PASS", evidence={"class_org_unit_id": class_id, "mapped_groups": len(group_mapping)})
        if not org_reasons
        else _gate("BLOCKED", *org_reasons)
    )

    members = {_text(row["id"]): row for row in tables["members"]}
    relations = tables["member_org_relations"]
    target_group_ids = set(group_mapping.values())
    relation_reasons: list[str] = []
    for relation in relations:
        if _text(relation.get("member_id")) not in members:
            relation_reasons.append("member_org_relations 引用了快照中不存在的 member")
            break
        if _text(relation.get("relation_type")) not in {"STUDY_CLASS", "STUDY_GROUP"}:
            relation_reasons.append("member_org_relations 出现了非白名单关系类型")
            break
        org_id = _text(relation.get("org_unit_id"))
        if org_id != class_id and org_id not in target_group_ids:
            relation_reasons.append("member_org_relations 超出目标班级和直属小组范围")
            break

    if credit_scope is None and relation_reasons:
        gates["MEMBER_RELATION_COVERAGE"] = _gate("BLOCKED", *sorted(set(relation_reasons)))
    elif credit_scope is None:
        gates["MEMBER_RELATION_COVERAGE"] = _gate(
            "NOT_RUN",
            "CREDIT_SCOPE_MEMBER_LIST_REQUIRED：需在安全身份确认后提供仅含 member_id/预期小组 ID 的私有范围文件",
            evidence={"relation_rows": len(relations)},
        )
    else:
        for item in credit_scope:
            member_id = item["member_id"]
            expected_group = item["expected_group_org_unit_id"]
            if member_id not in members:
                relation_reasons.append("credit scope 包含快照中不存在的 member")
                continue
            if expected_group and expected_group not in target_group_ids:
                relation_reasons.append("credit scope 的预期小组未在 source_group_mappings 中声明")
            for occurred_on in days:
                class_matches = [
                    row
                    for row in relations
                    if _text(row["member_id"]) == member_id
                    and _text(row["org_unit_id"]) == class_id
                    and _text(row["relation_type"]) == "STUDY_CLASS"
                    and _covers(row, occurred_on, "valid_from", "valid_until")
                ]
                group_matches = [
                    row
                    for row in relations
                    if _text(row["member_id"]) == member_id
                    and _text(row["org_unit_id"]) in target_group_ids
                    and _text(row["relation_type"]) == "STUDY_GROUP"
                    and _covers(row, occurred_on, "valid_from", "valid_until")
                ]
                if len(class_matches) != 1:
                    relation_reasons.append("积分范围成员在事实日期必须有唯一有效 STUDY_CLASS 关系")
                if len(group_matches) != 1:
                    relation_reasons.append("积分范围成员在事实日期必须有唯一有效 STUDY_GROUP 关系")
                elif expected_group and _text(group_matches[0]["org_unit_id"]) != expected_group:
                    relation_reasons.append("积分范围成员的小组关系与确认映射不一致")
    if credit_scope is not None:
        gates["MEMBER_RELATION_COVERAGE"] = (
            _gate("PASS", evidence={"credit_scope_members": len(credit_scope)})
            if not relation_reasons
            else _gate("BLOCKED", *sorted(set(relation_reasons)))
        )

    bindings = [
        row for row in tables["class_learning_bindings"] if _text(row["class_org_unit_id"]) == class_id
    ]
    cycles = [
        row for row in tables["class_learning_cycles"] if _text(row["class_org_unit_id"]) == class_id
    ]
    plan_ids = {_text(row["id"]) for row in tables["learning_plan_versions"]}
    round_reasons: list[str] = []
    frozen_rule_reasons: list[str] = []
    for occurred_on in days:
        active_bindings = [
            row for row in bindings if _covers(row, occurred_on, "started_at", "ended_at")
        ]
        if len(active_bindings) != 1:
            round_reasons.append("每个事实日期必须有唯一覆盖的 class_learning_binding")
            continue
        binding = active_bindings[0]
        if _text(binding.get("plan_version_id")) not in plan_ids:
            round_reasons.append("binding 引用了快照中不存在的 learning_plan_version")
        if not _text(binding.get("credit_rule_version_id")) or not _text(
            binding.get("course_credit_rule_version_id")
        ):
            frozen_rule_reasons.append("binding 缺少冻结的通用或课程学分规则版本")
        active_cycles = [
            row
            for row in cycles
            if _text(row.get("binding_id")) == _text(binding.get("id"))
            and _covers(row, occurred_on, "opened_at", "closed_at")
        ]
        if len(active_cycles) != 1:
            round_reasons.append("每个事实日期必须有唯一覆盖的 class_learning_cycle")
    gates["LEARNING_ROUND_COVERAGE"] = (
        _gate("PASS", evidence={"bindings": len(bindings), "cycles": len(cycles)})
        if not round_reasons
        else _gate("BLOCKED", *sorted(set(round_reasons)))
    )
    gates["FROZEN_RULE_COVERAGE"] = (
        _gate("PASS") if not frozen_rule_reasons else _gate("BLOCKED", *sorted(set(frozen_rule_reasons)))
    )

    _add_optional_dependency_gates(gates, tables, bindings, days)
    _add_reconciliation_placeholders(gates)
    return _final_report(snapshot_dir, class_name, start, end, gates, [])


def _add_optional_dependency_gates(
    gates: dict[str, dict[str, Any]],
    tables: dict[str, list[dict[str, str]]],
    bindings: list[dict[str, str]],
    days: list[date],
) -> None:
    rule_versions = tables.get("learning_credit_rule_versions")
    rules = tables.get("learning_credit_rules")
    if rule_versions is None and rules is None:
        gates["C7_FROZEN_RULE_EXPORT"] = _gate(
            "NOT_RUN", "ISOLATED_RUNTIME_DEPENDENCY_NOT_INCLUDED：仅在隔离库缺少冻结规则时导出"
        )
    elif rule_versions is None or rules is None:
        gates["C7_FROZEN_RULE_EXPORT"] = _gate("BLOCKED", "冻结规则导出必须同时包含版本与规则表")
    else:
        ids = {row["id"] for row in rule_versions}
        binding_ids = {
            _text(row.get("credit_rule_version_id")) for row in bindings if _text(row.get("credit_rule_version_id"))
        }
        missing = binding_ids - ids
        gates["C7_FROZEN_RULE_EXPORT"] = (
            _gate("PASS", evidence={"rule_versions": len(rule_versions), "rules": len(rules)})
            if not missing
            else _gate("BLOCKED", "binding 的冻结通用规则版本未在可选导出中出现")
        )

    calendar_versions = tables.get("learning_business_calendar_versions")
    calendar_days = tables.get("learning_business_calendar_days")
    if calendar_versions is None and calendar_days is None:
        gates["BUSINESS_CALENDAR_COVERAGE"] = _gate(
            "NOT_RUN", "ISOLATED_RUNTIME_DEPENDENCY_NOT_INCLUDED：仅在隔离库缺少 2026 日历时导出"
        )
    elif calendar_versions is None or calendar_days is None:
        gates["BUSINESS_CALENDAR_COVERAGE"] = _gate("BLOCKED", "业务日历导出必须同时包含版本与日期表")
    else:
        published = [
            row
            for row in calendar_versions
            if _text(row.get("calendar_key")) == "CHINA_MAINLAND"
            and _text(row.get("calendar_year")) == str(days[0].year)
            and _text(row.get("timezone")) == "Asia/Shanghai"
            and _text(row.get("status")) == "PUBLISHED"
        ]
        version_ids = {_text(row["id"]) for row in published}
        valid_dates = {
            _text(row.get("business_date"))
            for row in calendar_days
            if _text(row.get("calendar_version_id")) in version_ids
            and _text(row.get("day_type")) in VALID_DAY_TYPES
        }
        missing = [item.isoformat() for item in days if item.isoformat() not in valid_dates]
        gates["BUSINESS_CALENDAR_COVERAGE"] = (
            _gate("PASS", evidence={"calendar_versions": len(published), "covered_days": len(days)})
            if len(published) == 1 and not missing
            else _gate("BLOCKED", "2026 CHINA_MAINLAND PUBLISHED 日历未覆盖全部事实日期")
        )


def _add_reconciliation_placeholders(gates: dict[str, dict[str, Any]]) -> None:
    gates.setdefault(
        "HQ_IDENTITY_RECONCILIATION_CREDIT_SCOPE",
        _gate("NOT_RUN", "需在隔离库导入总部 Excel 后执行安全身份匹配"),
    )
    gates["NO_GROUP_IDENTITY_COVERAGE"] = _gate(
        "NOT_RUN / separately excluded",
        "空小组仅保留身份审计，不参与个人积分或班级率；需由导入结果证明",
    )
    gates["CLASS_RATE_RECONCILIATION"] = _gate(
        "NOT_RUN", "需在真实 Excel 与权威关系均已导入的隔离库中核对 39/39 基准"
    )
    gates["CREDIT_DRY_RUN_RECONCILIATION"] = _gate(
        "NOT_RUN", "需在 LEARNING_CREDIT_SETTLEMENT_ENABLED=false 的隔离库执行"
    )
    gates["ZERO_LEDGER_WRITE"] = _gate(
        "NOT_RUN", "需在最终 DRY-RUN 前后比较 learning_credit_entries 数量"
    )
    gates["REAL_FACT_RECONCILIATION"] = _gate(
        "BLOCKED / AWAITING_AUTHORIZED_SNAPSHOT_AND_ISOLATED_RECONCILIATION",
        "本工具只验证快照包，不生成来源事实、不执行 DRY-RUN、不写账本",
    )


def _final_report(
    snapshot_dir: Path,
    class_name: str,
    start: date,
    end: date,
    gates: dict[str, dict[str, Any]],
    errors: list[str],
) -> dict[str, Any]:
    required_preflight = (
        "PLATFORM_SNAPSHOT_SOURCE",
        "PLATFORM_SNAPSHOT_INTEGRITY",
        "ORG_HISTORICAL_COVERAGE",
        "LEARNING_ROUND_COVERAGE",
        "FROZEN_RULE_COVERAGE",
    )
    required_statuses = [gates.get(key, {}).get("status") for key in required_preflight]
    member_relation_status = gates.get("MEMBER_RELATION_COVERAGE", {}).get("status")
    if member_relation_status not in {None, "NOT_RUN"}:
        required_statuses.append(member_relation_status)
    status = "PASS" if all(item == "PASS" for item in required_statuses) else "BLOCKED"
    return {
        "snapshot_preflight_status": status,
        "snapshot_dir": str(snapshot_dir),
        "scope": {
            "class_name": class_name,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
        },
        "gates": gates,
        "errors": errors,
        "safety": {
            "database_connections": 0,
            "production_queries": 0,
            "ledger_writes": 0,
            "source_fact_writes": 0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 C7.1.2.3 权威只读快照包（不连接数据库）")
    parser.add_argument("--snapshot-dir", required=True, type=Path, help="私有快照目录")
    parser.add_argument("--class-name", default=DEFAULT_CLASS_NAME)
    parser.add_argument("--window-start", default=DEFAULT_WINDOW_START)
    parser.add_argument("--window-end", default=DEFAULT_WINDOW_END)
    parser.add_argument(
        "--credit-scope-file",
        type=Path,
        help="安全身份确认后提供的私有 member_id/预期小组 JSON；未提供时不声称人员覆盖通过",
    )
    parser.add_argument("--output", type=Path, help="可选的本地 JSON 报告路径")
    args = parser.parse_args()
    try:
        report = validate_snapshot(
            args.snapshot_dir,
            class_name=args.class_name,
            window_start=args.window_start,
            window_end=args.window_end,
            credit_scope_path=args.credit_scope_file,
        )
    except SnapshotValidationError as exc:
        report = {"snapshot_preflight_status": "BLOCKED", "errors": [str(exc)]}
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report.get("snapshot_preflight_status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
