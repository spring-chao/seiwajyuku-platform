from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids


def list_plans() -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT id, year, version, policy_text, status, write_enabled, business_approval_reference, "
        "created_at, updated_at FROM annual_plans ORDER BY year DESC, version DESC"
    )


def enable_plan_write(plan_id: int, actor_user_id: int, approval_reference: str) -> dict:
    reference = approval_reference.strip()
    if len(reference) < 6:
        raise ValueError("必须填写可追溯的业务批准依据")
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        plan = execute(connection, "SELECT * FROM annual_plans WHERE id=?", (plan_id,)).fetchone()
        if not plan:
            raise ValueError("年度方案不存在")
        before = dict(plan)
        execute(
            connection,
            "UPDATE annual_plans SET status='EXECUTING', write_enabled=1, "
            "business_approval_reference=?, updated_at=? WHERE id=?",
            (reference, now, plan_id),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="plans.enable_write",
            resource_type="annual_plan",
            resource_id=str(plan_id),
            purpose=reference,
            before=before,
            after={"status": "EXECUTING", "write_enabled": True},
        )
    return fetch_one("SELECT * FROM annual_plans WHERE id=?", (plan_id,))


def period_values(
    *, plan_id: int, user_id: int, month: int, org_unit_id: str | None = None
) -> list[dict[str, Any]]:
    allowed = accessible_org_ids(user_id)
    params: list[Any] = [plan_id, month]
    sql = (
        "SELECT v.id, v.org_unit_id, o.name AS org_name, d.metric_key, d.name AS metric_name, "
        "mv.unit, v.value_kind, v.numeric_value, v.value_state, v.source_type, "
        "v.is_manual_override, v.updated_at "
        "FROM metric_period_values v "
        "JOIN org_units o ON o.id=v.org_unit_id "
        "JOIN metric_versions mv ON mv.id=v.metric_version_id "
        "JOIN metric_definitions d ON d.id=mv.metric_definition_id "
        "WHERE v.annual_plan_id=? AND v.period_type='MONTH' AND v.period_no=?"
    )
    if org_unit_id:
        sql += " AND v.org_unit_id=?"
        params.append(org_unit_id)
    sql += " ORDER BY o.name, d.id, CASE v.value_kind WHEN 'MP' THEN 1 WHEN 'FORECAST' THEN 2 ELSE 3 END"
    rows = fetch_all(sql, tuple(params))
    if allowed is not None:
        rows = [row for row in rows if row["org_unit_id"] in allowed]
    return rows


def update_period_values(
    *, plan_id: int, user_id: int, updates: list[dict[str, Any]]
) -> int:
    plan = fetch_one("SELECT status, write_enabled FROM annual_plans WHERE id=?", (plan_id,))
    if not plan:
        raise ValueError("年度方案不存在")
    if not plan["write_enabled"] or plan["status"] != "EXECUTING":
        raise PermissionError("年度方案尚未获得业务批准，当前只读")
    allowed = accessible_org_ids(user_id)
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        changed = 0
        for item in updates:
            value_id = int(item["id"])
            current = execute(
                connection,
                "SELECT id, org_unit_id, value_kind, numeric_value, value_state FROM metric_period_values "
                "WHERE id=? AND annual_plan_id=?",
                (value_id, plan_id),
            ).fetchone()
            if not current:
                raise ValueError(f"期间值不存在: {value_id}")
            current = dict(current)
            if allowed is not None and current["org_unit_id"] not in allowed:
                raise PermissionError("不能修改授权组织范围之外的数据")
            if current["value_kind"] == "MP":
                raise PermissionError("月MP基准不可在执行期直接修改")
            numeric_value = item.get("numeric_value")
            state = item.get("value_state") or ("ZERO_IS_VALID" if numeric_value == 0 else "VALUE")
            if state in {"VALUE", "ZERO_IS_VALID"} and numeric_value is None:
                raise ValueError("有效数值状态必须填写数值")
            if state not in {"VALUE", "ZERO_IS_VALID", "NO_DATA", "NOT_APPLICABLE", "NOT_DUE"}:
                raise ValueError("未知空值状态")
            execute(
                connection,
                "UPDATE metric_period_values SET numeric_value=?, value_state=?, source_type='MANUAL', "
                "is_manual_override=1, updated_by=?, updated_at=? WHERE id=?",
                (numeric_value, state, user_id, now, value_id),
            )
            changed += 1
        write_audit(
            connection,
            actor_user_id=user_id,
            action="metrics.period_values.update",
            resource_type="annual_plan",
            resource_id=str(plan_id),
            after={"updated_count": changed, "value_ids": [int(item["id"]) for item in updates]},
        )
    return changed


def mp_dashboard(*, plan_id: int, user_id: int, month: int) -> dict[str, Any]:
    allowed = accessible_org_ids(user_id)
    centers = fetch_all(
        "SELECT DISTINCT o.id, o.name FROM org_units o "
        "JOIN metric_period_values v ON v.org_unit_id=o.id "
        "WHERE o.unit_type='REGIONAL_CENTER' AND o.is_active=1 AND v.annual_plan_id=? "
        "ORDER BY o.name",
        (plan_id,),
    )
    if allowed is not None:
        centers = [center for center in centers if center["id"] in allowed]
    rows = period_values(plan_id=plan_id, user_id=user_id, month=month)
    annual_targets = fetch_all(
        "SELECT t.org_unit_id, d.metric_key, t.annual_target, t.value_state "
        "FROM org_metric_targets t "
        "JOIN metric_versions mv ON mv.id=t.metric_version_id "
        "JOIN metric_definitions d ON d.id=mv.metric_definition_id "
        "WHERE t.annual_plan_id=?",
        (plan_id,),
    )
    annual_index = {
        (row["org_unit_id"], row["metric_key"]): row for row in annual_targets
    }
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["org_unit_id"], row["metric_key"])
        item = grouped.setdefault(
            key,
            {
                "org_unit_id": row["org_unit_id"],
                "org_name": row["org_name"],
                "metric_key": row["metric_key"],
                "metric_name": row["metric_name"],
                "unit": row["unit"],
                "mp": None,
                "forecast": None,
                "actual": None,
            },
        )
        field = row["value_kind"].lower()
        item[field] = {
            "value": row["numeric_value"],
            "state": row["value_state"],
        }
        target = annual_index.get(key)
        item["annual_target"] = target["annual_target"] if target else None
    items = list(grouped.values())
    for item in items:
        forecast = item.get("forecast") or {}
        actual = item.get("actual") or {}
        denominator = forecast.get("value")
        numerator = actual.get("value")
        item["forecast_achievement"] = (
            numerator / denominator
            if numerator is not None and denominator not in (None, 0)
            else None
        )
    return {"month": month, "centers": centers, "items": items}


def target_variances(plan_id: int, user_id: int) -> list[dict[str, Any]]:
    allowed = accessible_org_ids(user_id)
    if allowed is not None and "org-suzhou" not in allowed:
        return []
    targets = fetch_all(
        "SELECT t.org_unit_id, o.unit_type, d.metric_key, mv.aggregation_type, t.annual_target "
        "FROM org_metric_targets t JOIN org_units o ON o.id=t.org_unit_id "
        "JOIN metric_versions mv ON mv.id=t.metric_version_id "
        "JOIN metric_definitions d ON d.id=mv.metric_definition_id "
        "WHERE t.annual_plan_id=? AND t.annual_target IS NOT NULL",
        (plan_id,),
    )
    results = []
    metric_keys = sorted({row["metric_key"] for row in targets})
    for metric_key in metric_keys:
        root = next(
            (row["annual_target"] for row in targets if row["metric_key"] == metric_key and row["org_unit_id"] == "org-suzhou"),
            None,
        )
        children = [
            row["annual_target"] for row in targets
            if row["metric_key"] == metric_key and row["unit_type"] == "REGIONAL_CENTER"
        ]
        if root is None or not children:
            continue
        method = "SUM" if metric_key in {"active_member_count", "new_member_count"} else "AVG"
        aggregate = sum(children) if method == "SUM" else sum(children) / len(children)
        results.append({
            "metric_key": metric_key,
            "root_target": root,
            "child_aggregate": aggregate,
            "aggregation": method,
            "difference": root - aggregate,
        })
    return results
