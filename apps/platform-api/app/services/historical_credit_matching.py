"""Deterministic, history-aware matching for C2A staging previews.

Names and source groups are evidence only.  A row receives a member id only
when its class mapping is exact (or later explicitly confirmed) and one
member has an exact name plus a historical class relation covering the source
period.  This module never writes the ledger.
"""

from __future__ import annotations

import json
import unicodedata
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
MATCHING_ALGORITHM_VERSION = "C2A-HISTORICAL-MATCH-V1"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _normalized_name(value: Any) -> str:
    """Build a diagnostics-only normalized name key.

    A normalized name is only a candidate reason.  It never grants an
    automatic identity match.
    """

    normalized = unicodedata.normalize("NFKC", _text(value)).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _diagnose_member_candidates(
    *, tables: dict[str, Any], members: dict[str, dict[str, Any]],
    relations: list[dict[str, Any]], class_id: str, raw_name: Any,
    periods: list[tuple[date, date]],
) -> tuple[str, list[int]]:
    """Classify a deterministic miss without guessing an identity."""

    exact_global = [
        member for member in members.values()
        if _text(member.get("name")) == _text(raw_name)
    ]
    class_relations = [
        relation for relation in relations
        if _text(relation.get("org_unit_id")) == str(class_id)
        and _text(relation.get("relation_type")) == "STUDY_CLASS"
    ]
    class_member_ids = {str(relation.get("member_id")) for relation in class_relations}
    exact_in_class = [member for member in exact_global if str(member.get("id")) in class_member_ids]
    if exact_in_class:
        inactive_ids = [
            int(member["id"])
            for member in exact_in_class
            if _text(member.get("status") or "ACTIVE") != "ACTIVE"
        ]
        if inactive_ids:
            return "INACTIVE_MEMBER_CANDIDATE", sorted(set(inactive_ids))
        uncovered = [
            member for member in exact_in_class
            if not any(
                any(
                    _covers(relation, *period)
                    for relation in class_relations
                    if str(relation.get("member_id")) == str(member.get("id"))
                )
                for period in periods
            )
        ]
        if uncovered:
            return "INACTIVE_MEMBER_CANDIDATE", sorted(int(member["id"]) for member in uncovered)

    if len(exact_global) > 1:
        return "MULTIPLE_GLOBAL_CANDIDATES", sorted(int(member["id"]) for member in exact_global)
    if exact_global:
        center_id = next(
            (
                _text(candidate.get("parent_id"))
                for candidate in tables.get("class_candidates", [])
                if _text(candidate.get("id")) == str(class_id)
            ),
            "",
        )
        center_class_ids = {
            _text(candidate.get("id"))
            for candidate in tables.get("class_candidates", [])
            if center_id and _text(candidate.get("parent_id")) == center_id
        }
        center_member_ids = {
            _text(relation.get("member_id"))
            for relation in relations
            if _text(relation.get("org_unit_id")) in center_class_ids
            and _text(relation.get("relation_type")) == "STUDY_CLASS"
        }
        if str(exact_global[0].get("id")) in center_member_ids:
            return "MEMBER_EXACT_NAME_IN_CENTER", [int(exact_global[0]["id"])]
        return "MEMBER_EXACT_NAME_OUTSIDE_CLASS", [int(exact_global[0]["id"])]

    normalized_target = _normalized_name(raw_name)
    normalized_candidates = [
        member for member in members.values()
        if normalized_target and _normalized_name(member.get("name")) == normalized_target
    ]
    if normalized_candidates:
        if len(normalized_candidates) > 1:
            return "MULTIPLE_GLOBAL_CANDIDATES", sorted(int(member["id"]) for member in normalized_candidates)
        return "MEMBER_NORMALIZED_NAME_CANDIDATE", [int(normalized_candidates[0]["id"])]
    return "NO_MEMBER_CANDIDATE", []


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
                "mapping_reason": "SOURCE_IS_GROUP_LABEL",
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
                    reason = "INACTIVE_MEMBER_CANDIDATE"
                else:
                    outcome = IDENTITY_AUTO_MATCHED
                    db_match_status = "AUTO_MATCHED"
                    member_id = candidate_member_ids[0]
                    reason = "EXACT_NAME_UNIQUE_WITH_HISTORICAL_CLASS_RELATION"
            elif len(candidate_member_ids) > 1:
                outcome = IDENTITY_AMBIGUOUS
                db_match_status = "AMBIGUOUS"
                reason = "MULTIPLE_GLOBAL_CANDIDATES"
            else:
                outcome = IDENTITY_NOT_FOUND
                db_match_status = "NOT_FOUND"
                reason, candidate_member_ids = _diagnose_member_candidates(
                    tables=tables,
                    members=members,
                    relations=relations,
                    class_id=str(class_id),
                    raw_name=source_row.get("raw_name"),
                    periods=_row_periods(source_row),
                )
        elif mapping.get("mapping_status") == "AMBIGUOUS":
            outcome = IDENTITY_AMBIGUOUS
            db_match_status = "AMBIGUOUS"
            reason = "CLASS_MAPPING_BLOCKED"
        elif mapping.get("mapping_status") == "NOT_FOUND":
            reason = str(mapping.get("mapping_reason") or "CLASS_MAPPING_BLOCKED")
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
    connection: Any, *, batch_id: int, results: list[dict[str, Any]], actor_user_id: int | None = None,
    snapshot_id: str | None = None, snapshot_fingerprint: str | None = None,
    algorithm_version: str = MATCHING_ALGORITHM_VERSION,
) -> dict[str, int]:
    """Update only local staging rows/items; no ledger table is touched."""

    now = datetime.now(timezone.utc).isoformat()
    updated_rows = 0
    updated_items = 0
    mapping_count = 0
    by_key = {(item["source_sheet"], item["source_row_number"]): item for item in results}
    mappings: dict[tuple[str, str | None], dict[str, Any]] = {}
    for match in results:
        mapping = match.get("class_mapping") or {}
        key = (str(mapping.get("source_sheet") or match.get("source_sheet") or ""), mapping.get("raw_class_name"))
        mappings[key] = mapping
    for (source_sheet, raw_class_name), mapping in mappings.items():
        candidate_ids = [str(value) for value in mapping.get("candidate_org_unit_ids", [])]
        if raw_class_name is None:
            existing = execute(
                connection,
                "SELECT id FROM learning_credit_import_class_mappings "
                "WHERE batch_id=? AND source_sheet=? AND raw_class_name IS NULL",
                (batch_id, source_sheet),
            ).fetchone()
        else:
            existing = execute(
                connection,
                "SELECT id FROM learning_credit_import_class_mappings "
                "WHERE batch_id=? AND source_sheet=? AND raw_class_name=?",
                (batch_id, source_sheet, raw_class_name),
            ).fetchone()
        values = (
            json.dumps(candidate_ids, ensure_ascii=False), mapping.get("org_unit_id"),
            mapping.get("mapping_status") or "PENDING_REVIEW",
            mapping.get("mapping_reason") or "MAPPING_REVIEW_REQUIRED",
            snapshot_id, snapshot_fingerprint, json.dumps(mapping, ensure_ascii=False), now,
        )
        if existing:
            execute(
                connection,
                "UPDATE learning_credit_import_class_mappings SET candidate_org_unit_ids_json=?, "
                "confirmed_org_unit_id=?, mapping_status=?, mapping_reason=?, snapshot_id=?, "
                "snapshot_fingerprint=?, evidence_json=?, updated_at=? WHERE id=?",
                (*values, existing["id"]),
            )
        else:
            execute(
                connection,
                "INSERT INTO learning_credit_import_class_mappings "
                "(batch_id, source_sheet, raw_class_name, candidate_org_unit_ids_json, confirmed_org_unit_id, "
                "mapping_status, mapping_reason, snapshot_id, snapshot_fingerprint, evidence_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (batch_id, source_sheet, raw_class_name, *values, now),
            )
        mapping_count += 1
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
                "match_snapshot_id": snapshot_id,
                "match_snapshot_fingerprint": snapshot_fingerprint,
                "match_algorithm_version": algorithm_version,
            }
        )
        execute(
            connection,
            "UPDATE learning_credit_import_rows SET matched_member_id=?, match_status=?, match_reason=?, metadata_json=?, "
            "match_snapshot_id=?, match_snapshot_fingerprint=?, match_algorithm_version=?, updated_at=? WHERE id=?",
            (
                match["matched_member_id"],
                match["match_status"],
                match["match_reason"],
                json.dumps(metadata, ensure_ascii=False),
                snapshot_id,
                snapshot_fingerprint,
                algorithm_version,
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
    return {
        "updated_rows": updated_rows,
        "updated_items": updated_items,
        "class_mapping_count": mapping_count,
        "ledger_writes": 0,
    }
