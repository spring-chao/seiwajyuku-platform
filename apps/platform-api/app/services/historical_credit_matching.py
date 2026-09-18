"""Deterministic, history-aware matching for C2A staging previews.

Names and source groups are evidence only.  A row receives a member id only
when its class mapping is exact (or later explicitly confirmed) and one
member has an exact name plus a historical class relation covering the source
period.  This module never writes the ledger.
"""

from __future__ import annotations

import json
from calendar import monthrange
from datetime import date, datetime, timezone
from typing import Any

from app.db import execute


SOURCE_SHEET_CENTERS = {
    "黄埔二班": "org-suzhou",
    "昆山": "org-kunshan",
    "吴江": "org-wujiang",
    "园区": "org-yuanqu",
    "张家港": "org-zhangjiagang",
    "姑苏相城": "org-gusu",
    "新吴": "org-xinwu",
}

# These are intentionally not auto-confirmed.  They document a candidate
# alias for the business owner; a later confirmation can promote the mapping.
PLATFORM_CLASS_ALIAS_CANDIDATES = {
    ("姑苏相城", "不一班"): "苏州不一班",
    ("姑苏相城", "真干一班"): "真干班",
}

IDENTITY_AUTO_MATCHED = "AUTO_MATCHED"
IDENTITY_CONFIRMED = "CONFIRMED"
IDENTITY_AMBIGUOUS = "AMBIGUOUS"
IDENTITY_NOT_FOUND = "NOT_FOUND"
IDENTITY_CONFLICT = "CONFLICT"
IDENTITY_HISTORICAL_UNKNOWN = "HISTORICAL_RELATION_UNKNOWN"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _parse_date(value: Any) -> date | None:
    value = _text(value)
    if not value:
        return None
    return date.fromisoformat(value[:10])


def _covers(row: dict[str, Any], start: date, end: date) -> bool:
    valid_from = _parse_date(row.get("valid_from"))
    valid_until = _parse_date(row.get("valid_until"))
    return (valid_from is None or valid_from <= start) and (valid_until is None or valid_until >= end)


def _source_months(source_row: dict[str, Any]) -> list[int]:
    return sorted(
        {
            int(item.get("source_month"))
            for item in source_row.get("items", [])
            if item.get("source_month") is not None
        }
    )


def _row_periods(source_row: dict[str, Any]) -> list[tuple[date, date]]:
    months = _source_months(source_row)
    if not months:
        return [(date(2026, 1, 1), date(2026, 12, 31))]
    return [
        (date(2026, month, 1), date(2026, month, monthrange(2026, month)[1]))
        for month in months
    ]


def build_historical_class_mappings(
    *, snapshot: dict[str, Any], source_rows: list[dict[str, Any]]
) -> dict[tuple[str, str], dict[str, Any]]:
    tables = snapshot.get("tables", snapshot)
    candidates = tables.get("class_candidates", [])
    seen = {(str(row.get("source_sheet") or ""), str(row.get("raw_class_name") or "")) for row in source_rows}
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for source_sheet, raw_class_name in sorted(seen):
        center_id = SOURCE_SHEET_CENTERS.get(source_sheet)
        if not raw_class_name:
            result[(source_sheet, raw_class_name)] = {
                "source_sheet": source_sheet,
                "raw_class_name": None,
                "org_unit_id": None,
                "mapping_status": "NOT_FOUND",
                "mapping_reason": "SOURCE_CLASS_MISSING",
                "candidate_org_unit_ids": [],
            }
            continue
        if raw_class_name == "精进组":
            result[(source_sheet, raw_class_name)] = {
                "source_sheet": source_sheet,
                "raw_class_name": raw_class_name,
                "org_unit_id": None,
                "mapping_status": "NOT_FOUND",
                "mapping_reason": "SOURCE_CLASS_IS_GROUP_LABEL",
                "candidate_org_unit_ids": [],
            }
            continue
        exact = [
            row
            for row in candidates
            if _text(row.get("name")) == raw_class_name
            and (center_id is None or _text(row.get("parent_id")) == center_id)
        ]
        alias_name = PLATFORM_CLASS_ALIAS_CANDIDATES.get((source_sheet, raw_class_name))
        if alias_name:
            alias_candidates = [
                row
                for row in candidates
                if _text(row.get("name")) == alias_name
                and (center_id is None or _text(row.get("parent_id")) == center_id)
            ]
            result[(source_sheet, raw_class_name)] = {
                "source_sheet": source_sheet,
                "raw_class_name": raw_class_name,
                "org_unit_id": None,
                "mapping_status": "AMBIGUOUS",
                "mapping_reason": "UNCONFIRMED_PLATFORM_CLASS_ALIAS",
                "candidate_org_unit_ids": [str(row.get("id")) for row in alias_candidates],
                "candidate_names": [alias_name],
            }
            continue
        if len(exact) == 1:
            row = exact[0]
            result[(source_sheet, raw_class_name)] = {
                "source_sheet": source_sheet,
                "raw_class_name": raw_class_name,
                "org_unit_id": str(row.get("id")),
                "mapping_status": "EXACT",
                "mapping_reason": "CENTER_SCOPED_EXACT_CLASS_NAME",
                "candidate_org_unit_ids": [str(row.get("id"))],
            }
        elif not exact:
            result[(source_sheet, raw_class_name)] = {
                "source_sheet": source_sheet,
                "raw_class_name": raw_class_name,
                "org_unit_id": None,
                "mapping_status": "NOT_FOUND",
                "mapping_reason": "NO_CENTER_SCOPED_CLASS_CANDIDATE",
                "candidate_org_unit_ids": [],
            }
        else:
            result[(source_sheet, raw_class_name)] = {
                "source_sheet": source_sheet,
                "raw_class_name": raw_class_name,
                "org_unit_id": None,
                "mapping_status": "AMBIGUOUS",
                "mapping_reason": "MULTIPLE_HISTORICAL_CLASS_CANDIDATES",
                "candidate_org_unit_ids": [str(row.get("id")) for row in exact],
            }
    return result


def match_historical_rows(
    *, snapshot: dict[str, Any], source_rows: list[dict[str, Any]], class_mappings: dict[tuple[str, str], dict[str, Any]]
) -> list[dict[str, Any]]:
    tables = snapshot.get("tables", snapshot)
    members = {str(row.get("id")): row for row in tables.get("members", [])}
    relations = tables.get("member_org_relations", [])
    results: list[dict[str, Any]] = []
    for source_row in source_rows:
        source_sheet = str(source_row.get("source_sheet") or "")
        raw_class = str(source_row.get("raw_class_name") or "")
        mapping = class_mappings[(source_sheet, raw_class)]
        outcome = IDENTITY_NOT_FOUND
        db_match_status = "NOT_FOUND"
        member_id: int | None = None
        candidate_member_ids: list[int] = []
        reason = str(mapping.get("mapping_reason") or "MEMBER_MAPPING_REQUIRED")
        class_id = mapping.get("org_unit_id")
        if mapping.get("mapping_status") == "EXACT" and class_id:
            class_relations = [
                relation
                for relation in relations
                if _text(relation.get("org_unit_id")) == str(class_id)
                and _text(relation.get("relation_type")) == "STUDY_CLASS"
            ]
            by_name = {
                member_id: member
                for member_id, member in members.items()
                if _text(member.get("name")) == _text(source_row.get("raw_name"))
            }
            for relation in class_relations:
                candidate_id = str(relation.get("member_id"))
                if candidate_id in by_name:
                    candidate_member_ids.append(int(candidate_id))
            candidate_member_ids = sorted(set(candidate_member_ids))
            if len(candidate_member_ids) == 1:
                selected = str(candidate_member_ids[0])
                selected_relations = [
                    relation for relation in class_relations if str(relation.get("member_id")) == selected
                ]
                uncovered = [
                    period
                    for period in _row_periods(source_row)
                    if not any(_covers(relation, *period) for relation in selected_relations)
                ]
                if uncovered:
                    outcome = IDENTITY_HISTORICAL_UNKNOWN
                    db_match_status = "CONFLICT"
                    reason = "HISTORICAL_RELATION_UNKNOWN"
                else:
                    outcome = IDENTITY_AUTO_MATCHED
                    db_match_status = "AUTO_MATCHED"
                    member_id = candidate_member_ids[0]
                    reason = "EXACT_NAME_UNIQUE_WITH_HISTORICAL_CLASS_RELATION"
            elif len(candidate_member_ids) > 1:
                outcome = IDENTITY_AMBIGUOUS
                db_match_status = "AMBIGUOUS"
                reason = "DUPLICATE_EXACT_NAMES_WITHIN_CLASS"
            else:
                outcome = IDENTITY_NOT_FOUND
                db_match_status = "NOT_FOUND"
                reason = "MEMBER_MAPPING_REQUIRED"
        elif mapping.get("mapping_status") == "AMBIGUOUS":
            outcome = IDENTITY_AMBIGUOUS
            db_match_status = "AMBIGUOUS"
            reason = str(mapping.get("mapping_reason") or "HISTORICAL_CLASS_MAPPING_AMBIGUOUS")
        result = {
            "source_sheet": source_sheet,
            "source_row_number": int(source_row["source_row_number"]),
            "raw_name": source_row.get("raw_name"),
            "raw_class_name": source_row.get("raw_class_name"),
            "raw_group_name": source_row.get("raw_group_name"),
            "class_mapping": mapping,
            "identity_status": outcome,
            "match_status": db_match_status,
            "matched_member_id": member_id,
            "candidate_member_ids": candidate_member_ids,
            "match_reason": reason,
            "identity_ready": member_id is not None,
            "credit_validation_status": source_row.get("validation_status"),
            "credit_ready": source_row.get("validation_status") == "PASS",
        }
        results.append(result)
    return results


def summarize_matching(results: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = [
        IDENTITY_AUTO_MATCHED,
        IDENTITY_CONFIRMED,
        IDENTITY_AMBIGUOUS,
        IDENTITY_NOT_FOUND,
        IDENTITY_CONFLICT,
        IDENTITY_HISTORICAL_UNKNOWN,
    ]
    identity_counts = {status: sum(item["identity_status"] == status for item in results) for status in statuses}
    credit_counts = {
        status: sum(item["credit_validation_status"] == status for item in results)
        for status in ("PASS", "TOTAL_MISSING", "DETAIL_MISSING", "MISMATCH", "ZERO")
    }
    return {
        "TOTAL_ROWS": len(results),
        **identity_counts,
        "CREDIT_VALIDATION": credit_counts,
        "identity_ready_rows": sum(item["identity_ready"] for item in results),
        "credit_validation_ready_rows": sum(item["credit_ready"] for item in results),
        "identity_and_credit_ready_rows": sum(item["identity_ready"] and item["credit_ready"] for item in results),
    }


def apply_matches_to_staging(
    connection: Any, *, batch_id: int, results: list[dict[str, Any]], actor_user_id: int | None = None
) -> dict[str, int]:
    """Update only local staging rows/items; no ledger table is touched."""

    now = datetime.now(timezone.utc).isoformat()
    updated_rows = 0
    updated_items = 0
    by_key = {(item["source_sheet"], item["source_row_number"]): item for item in results}
    row_cursor = execute(
        connection,
        "SELECT id, source_sheet, source_row_number, metadata_json FROM learning_credit_import_rows WHERE batch_id=?",
        (batch_id,),
    )
    for row in row_cursor.fetchall():
        key = (row["source_sheet"], int(row["source_row_number"]))
        match = by_key.get(key)
        if match is None:
            continue
        metadata = json.loads(row["metadata_json"] or "{}")
        metadata.update(
            {
                "identity_status": match["identity_status"],
                "candidate_member_ids": match["candidate_member_ids"],
                "class_mapping": match["class_mapping"],
            }
        )
        execute(
            connection,
            "UPDATE learning_credit_import_rows SET matched_member_id=?, match_status=?, match_reason=?, metadata_json=?, updated_at=? WHERE id=?",
            (
                match["matched_member_id"],
                match["match_status"],
                match["match_reason"],
                json.dumps(metadata, ensure_ascii=False),
                now,
                row["id"],
            ),
        )
        updated_rows += 1
        execute(
            connection,
            "UPDATE learning_credit_import_items SET matched_member_id=?, updated_at=? WHERE batch_id=? AND import_row_id=?",
            (match["matched_member_id"], now, batch_id, row["id"]),
        )
        updated_items += int(
            execute(
                connection,
                "SELECT COUNT(*) AS count FROM learning_credit_import_items WHERE batch_id=? AND import_row_id=?",
                (batch_id, row["id"]),
            ).fetchone()["count"]
        )
    return {"updated_rows": updated_rows, "updated_items": updated_items, "ledger_writes": 0}
