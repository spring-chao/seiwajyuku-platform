from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

from app.core.privacy import normalize_phone, phone_hash
from app.core.settings import get_settings
from app.db import execute, fetch_all, fetch_one, transaction
from app.services.iam import accessible_org_ids


METRIC_BY_ACTIVITY = {
    ("READING", "CHECKIN"): "reading_checkin_rate",
    ("ATTENDANCE", "CLASS_MEETING"): "class_meeting_rate",
    ("ATTENDANCE", "GROUP_MEETING"): "group_meeting_rate",
    ("ATTENDANCE", "STAFF_TRAINING"): "staff_training_rate",
    ("ATTENDANCE", "BOARD"): "board_attendance_rate",
}


def _key_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def verify_integration_key(api_key: str) -> None:
    expected = get_settings().integration_api_key
    if not expected or not hmac.compare_digest(api_key or "", expected):
        raise PermissionError("集成接口密钥无效")


def _ensure_source(connection, source_key: str, snapshot_type: str, now: str) -> None:
    current = execute(
        connection,
        "SELECT source_key, source_type, is_active FROM integration_sources WHERE source_key=?",
        (source_key,),
    ).fetchone()
    if current and not current["is_active"]:
        raise PermissionError("集成数据源已停用")
    if current and current["source_type"] != snapshot_type:
        raise ValueError("数据源类型与快照类型不一致")
    if not current:
        execute(
            connection,
            "INSERT INTO integration_sources(source_key, source_type, api_key_hash, is_active, "
            "created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
            (source_key, snapshot_type, _key_hash(get_settings().integration_api_key), now, now),
        )


def ingest_snapshots(
    *,
    source_key: str,
    snapshot_type: str,
    api_key: str,
    events: list[dict[str, Any]],
) -> dict[str, int]:
    verify_integration_key(api_key)
    snapshot_type = snapshot_type.upper()
    if snapshot_type not in {"ATTENDANCE", "READING"}:
        raise ValueError("未知快照类型")
    started = datetime.now(UTC).isoformat()
    inserted = 0
    duplicates = 0
    with transaction() as connection:
        _ensure_source(connection, source_key, snapshot_type, started)
        for event in events:
            external_id = str(event["external_id"]).strip()
            if not external_id:
                raise ValueError("external_id 不能为空")
            if execute(
                connection,
                "SELECT id FROM integration_snapshots WHERE source_key=? AND external_id=?",
                (source_key, external_id),
            ).fetchone():
                duplicates += 1
                continue
            org = execute(
                connection, "SELECT id FROM org_units WHERE id=?", (event["org_unit_id"],)
            ).fetchone()
            if not org:
                raise ValueError(f"组织不存在: {event['org_unit_id']}")
            eligible = int(event.get("eligible_count") or 0)
            completed = int(event.get("completed_count") or 0)
            if eligible < 0 or completed < 0 or completed > eligible:
                raise ValueError("参与人数必须满足 0 <= completed_count <= eligible_count")
            raw_phone = event.get("participant_phone")
            participant_ref = event.get("participant_ref")
            participant_digest = None
            participant_last4 = None
            if raw_phone:
                normalized = normalize_phone(str(raw_phone))
                participant_digest = phone_hash(normalized)
                participant_last4 = normalized[-4:]
            elif participant_ref:
                participant_digest = hashlib.sha256(str(participant_ref).encode("utf-8")).hexdigest()
            execute(
                connection,
                "INSERT INTO integration_snapshots(source_key, external_id, snapshot_type, "
                "org_unit_id, activity_type, occurred_at, participant_hash, participant_last4, "
                "eligible_count, completed_count, title, status, received_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    source_key, external_id, snapshot_type, event["org_unit_id"],
                    str(event["activity_type"]).upper(), event["occurred_at"],
                    participant_digest, participant_last4, eligible, completed,
                    event.get("title"), str(event.get("status") or "COMPLETED").upper(), started,
                ),
            )
            inserted += 1
        finished = datetime.now(UTC).isoformat()
        execute(
            connection,
            "INSERT INTO integration_sync_runs(source_key, snapshot_type, received_count, "
            "inserted_count, duplicate_count, status, started_at, finished_at) "
            "VALUES (?, ?, ?, ?, ?, 'SUCCESS', ?, ?)",
            (source_key, snapshot_type, len(events), inserted, duplicates, started, finished),
        )
    return {"received": len(events), "inserted": inserted, "duplicates": duplicates}


def calculate_monthly_metrics(
    *, annual_plan_id: int, source_key: str, year: int, month: int
) -> list[dict[str, Any]]:
    plan = fetch_one(
        "SELECT id, status, write_enabled FROM annual_plans WHERE id=?", (annual_plan_id,)
    )
    if not plan:
        raise ValueError("年度方案不存在")
    if plan["status"] != "EXECUTING" or not plan["write_enabled"]:
        raise PermissionError("年度方案未获业务批准，自动指标仅完成快照同步，不写入实绩")
    period_prefix = f"{year:04d}-{month:02d}"
    grouped = fetch_all(
        "SELECT snapshot_type, activity_type, org_unit_id, "
        "SUM(completed_count) AS numerator, SUM(eligible_count) AS denominator "
        "FROM integration_snapshots WHERE source_key=? AND substr(occurred_at, 1, 7)=? "
        "AND status='COMPLETED' GROUP BY snapshot_type, activity_type, org_unit_id",
        (source_key, period_prefix),
    )
    now = datetime.now(UTC).isoformat()
    results: list[dict[str, Any]] = []
    with transaction() as connection:
        for row in grouped:
            metric_key = METRIC_BY_ACTIVITY.get((row["snapshot_type"], row["activity_type"]))
            if not metric_key:
                continue
            metric = execute(
                connection,
                "SELECT mv.id AS metric_version_id FROM metric_versions mv "
                "JOIN metric_definitions d ON d.id=mv.metric_definition_id "
                "JOIN plan_metrics pm ON pm.metric_version_id=mv.id "
                "WHERE pm.annual_plan_id=? AND d.metric_key=?",
                (annual_plan_id, metric_key),
            ).fetchone()
            if not metric:
                continue
            numerator = float(row["numerator"] or 0)
            denominator = float(row["denominator"] or 0)
            value = numerator / denominator if denominator else None
            current = execute(
                connection,
                "SELECT id, is_manual_override FROM metric_period_values "
                "WHERE annual_plan_id=? AND org_unit_id=? AND metric_version_id=? "
                "AND period_type='MONTH' AND period_no=? AND value_kind='ACTUAL'",
                (annual_plan_id, row["org_unit_id"], metric["metric_version_id"], month),
            ).fetchone()
            result = "UPDATED"
            if current and current["is_manual_override"]:
                result = "MANUAL_OVERRIDE_PRESERVED"
            elif current:
                execute(
                    connection,
                    "UPDATE metric_period_values SET numeric_value=?, value_state=?, "
                    "source_type='AUTO_INTEGRATION', source_reference=?, calculation_detail_json=?, "
                    "updated_at=? WHERE id=?",
                    (
                        value, "VALUE" if value is not None else "NO_DATA", source_key,
                        json.dumps({"numerator": numerator, "denominator": denominator}),
                        now, current["id"],
                    ),
                )
            else:
                execute(
                    connection,
                    "INSERT INTO metric_period_values(annual_plan_id, org_unit_id, metric_version_id, "
                    "period_type, period_no, value_kind, numeric_value, value_state, source_type, "
                    "source_reference, calculation_detail_json, is_manual_override, updated_at) "
                    "VALUES (?, ?, ?, 'MONTH', ?, 'ACTUAL', ?, ?, 'AUTO_INTEGRATION', ?, ?, 0, ?)",
                    (
                        annual_plan_id, row["org_unit_id"], metric["metric_version_id"], month,
                        value, "VALUE" if value is not None else "NO_DATA", source_key,
                        json.dumps({"numerator": numerator, "denominator": denominator}), now,
                    ),
                )
            execute(
                connection,
                "INSERT INTO metric_calculation_runs(metric_version_id, org_unit_id, period_type, "
                "period_no, numerator, denominator, result_value, source_period, detail_json, "
                "status, created_at) VALUES (?, ?, 'MONTH', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    metric["metric_version_id"], row["org_unit_id"], month, numerator,
                    denominator, value, period_prefix,
                    json.dumps({"source_key": source_key, "metric_key": metric_key}),
                    result, now,
                ),
            )
            results.append({
                "org_unit_id": row["org_unit_id"],
                "metric_key": metric_key,
                "value": value,
                "result": result,
            })
    return results


def activity_admin_view(user_id: int, month: str | None = None) -> list[dict[str, Any]]:
    params: tuple[Any, ...] = (month,) if month else ()
    rows = fetch_all(
        "SELECT id, source_key, external_id, snapshot_type, org_unit_id, activity_type, "
        "occurred_at, eligible_count, completed_count, title, status, received_at "
        "FROM integration_snapshots "
        + ("WHERE substr(occurred_at, 1, 7)=? " if month else "")
        + "ORDER BY occurred_at DESC, id DESC",
        params,
    )
    allowed = accessible_org_ids(user_id)
    if allowed is not None:
        rows = [row for row in rows if row["org_unit_id"] in allowed]
    return rows
