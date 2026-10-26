"""Mobile facade to the existing authoritative signin management engine."""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids, mobile_iam_context
from app.services import signin_engine
from app.services.wechat_operations import _principal, _source_principal
from app.services.wechat_operations_v2 import writable


def _perform(principal, operation, values):
    from app.api.attendance_manage import perform
    permission = signin_engine.required_permissions(operation, values)[0]
    scoped = _source_principal(principal, permission)
    with mobile_iam_context(scoped, permission):
        return perform(operation, values, {**scoped, "id": int(principal["user_id"])})


def _allowed(principal, permission, org_id):
    with mobile_iam_context(_source_principal(principal, permission), permission):
        allowed = accessible_org_ids(int(principal["user_id"]))
    return allowed is None or org_id in allowed


def context(session):
    principal = _principal(session)
    can_view = "attendance:view" in principal["permissions"]
    can_create = "attendance:create" in principal["permissions"]
    if not can_view and not can_create:
        return {"organizations":[],"classes":[],"can_view":False,"can_create":False,
            "permission_message":"请管理员在员工管理中按当前服务范围授予‘活动签到运营’能力；页面不会自动扩大组织范围或授予权限。"}
    rows = fetch_all("SELECT id,name,unit_type FROM org_units WHERE is_active=1 ORDER BY name")
    organizations = [row for row in rows if "attendance:create" in principal["permissions"]
                     and _allowed(principal, "attendance:create", row["id"])]
    from app.services.study_membership import CURRENT_CLASS_ID_SQL
    counts = {r["class_id"]: r["n"] for r in fetch_all(f"SELECT {CURRENT_CLASS_ID_SQL} AS class_id,COUNT(*) AS n FROM members m WHERE m.status='ACTIVE' GROUP BY class_id")}
    classes = [{**row, "member_count": counts.get(row["id"], 0)} for row in organizations if row["unit_type"] == "CLASS"]
    return {"organizations": organizations, "classes": classes, "can_view":can_view,"can_create":can_create,
        "activity_types": [{"value": value, "label": label} for value, label in (
            ("class_meeting", "班级学习会（三场）"), ("course", "课程"), ("national_report", "全国报告会"),
            ("center_quarterly_report", "分中心季度报告会"), ("staff_training", "班主任辅导员培训会"),
            ("board_meeting", "理事会"), ("study_tour", "游学"), ("other", "其他活动"))]}


def _normalized(event):
    return {**event, "id": str(event["event_id"]), "title": event.get("name") or event.get("event_name") or "未命名活动",
            "status": event.get("lifecycle_status", "DRAFT"),
            "org_name": event.get("group_name") or event.get("class_name") or event.get("center_name") or "",
            "participant_count": event.get("total", 0)}


def list_activities(session, month, page=1):
    principal = _principal(session)
    start = date.fromisoformat(month + "-01")
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1) - timedelta(days=1)
    data = _perform(principal, "admin_events", {"date_from": start.isoformat(), "date_to": end.isoformat(), "page": page, "page_size": 20})
    records = []
    for row in data.get("items", data.get("events", [])):
        sessions = row.get("sessions") or [row]
        preferred = next((event for event in sessions if event.get("checkin_status") == "open"), sessions[0])
        records.append({**_normalized({**row, **preferred}), "title": row.get("name") or row.get("event_name") or preferred.get("name") or "未命名活动", "session_count": len(sessions)})
    return {"records": records, "has_more": bool(data.get("has_more")), "page": page}


def _event(principal, event_id, permission):
    scoped = _source_principal(principal, permission)
    with mobile_iam_context(scoped, permission):
        signin_engine.enabled()
        return signin_engine.event_detail(event_id, user={**scoped, "id": int(principal["user_id"])}, permission=permission)


def detail(session, event_id):
    principal = _principal(session)
    data = _perform(principal, "event_detail", {"event_id": event_id})
    event = data.get("event") or (data.get("data") or {}).get("event")
    if not event:
        raise ValueError("活动不存在")
    owner = event.get("group_org_unit_id") or event.get("class_org_unit_id") or event.get("org_unit_id")
    capabilities = {key: permission in principal["permissions"] and _allowed(principal, permission, owner)
                    for key, permission in (("can_update", "attendance:update"), ("can_manage", "attendance:manage"), ("can_status", "attendance:status"), ("can_code", "attendance:code"), ("can_sync_roster", "attendance:import"))}
    statistics, statistics_error = None, ""
    try:
        statistics = _perform(principal, "stats", {"event_id": event_id})
    except Exception as exc:
        from fastapi import HTTPException
        if not isinstance(exc, (signin_engine.SigninEngineError, HTTPException)) or exc.status_code not in {502, 503}:
            raise
        statistics_error = "统计暂不可用，请刷新；活动与人员明细仍可查看。"
    capabilities["can_edit_times"] = capabilities["can_update"] and not any(row.get("checked") or row.get("checked_at") for row in data.get("rows", []))
    return {"group": _normalized(event), "attendees": data.get("rows", []), **capabilities,
            "sessions": [_normalized(row) for row in data.get("sessions", [event])], "statistics": statistics,
            "statistics_error": statistics_error,
            "revision": hashlib.sha256(json.dumps(event, sort_keys=True, default=str).encode()).hexdigest()}


def _once(principal, operation, request_id, payload, target):
    """Durable intent before remote writes; unknown outcomes are never reissued.

    A successful receipt can be replayed. A timeout requires reading the engine
    state first, since a local rollback cannot undo a remote registration.
    """
    actor = int(principal["user_id"])
    writable()
    permission = signin_engine.OPERATIONS[operation]
    with mobile_iam_context(_source_principal(principal, permission), permission):
        signin_engine.enabled(write=True)
        signin_engine.validate_organizations(payload, signin_engine.allowed_scope(actor, permission), required=operation in {"create_event", "create_class_meeting_sessions"})
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    action = "wechat.mobile.signin." + operation
    with transaction() as connection:
        execute(connection, "UPDATE app_users SET id=id WHERE id=?", (actor,))
        prior = execute(connection, "SELECT result,after_json FROM audit_logs WHERE actor_user_id=? AND action=? AND request_id=? ORDER BY id DESC LIMIT 1", (actor, action, request_id)).fetchone()
        if prior:
            receipt = json.loads(prior["after_json"] or "{}")
            if receipt.get("digest") != digest:
                raise ValueError("提交内容已变化，请返回活动列表核对后重试")
            if prior["result"] == "SUCCESS":
                return {**receipt["receipt"], "replayed": True}
            raise ValueError("上次请求结果待核对，请返回活动列表刷新后确认，勿重复创建或报名")
        write_audit(connection, actor_user_id=actor, action=action, resource_type="signin_event",
                    resource_id=target, request_id=request_id, result="PENDING", after={"digest": digest})
    result = _perform(principal, operation, payload)
    created = result.get("events") or (result.get("data") or {}).get("events") or []
    receipt = {"id": str(result.get("event_id") or (result.get("data") or {}).get("event_id") or (created[0].get("event_id") if created else None) or target or ""),
               "saved": True, "event_group_id": result.get("event_group_id"), "session_count": len(created) or 1}
    with transaction() as connection:
        write_audit(connection, actor_user_id=actor, action=action, resource_type="signin_event",
                    resource_id=receipt["id"] or None, request_id=request_id,
                    after={"digest": digest, "receipt": receipt})
    return receipt


def save(session, values, event_id=None):
    principal = _principal(session)
    writable()
    title = values["title"].strip()
    if not title:
        raise ValueError("请填写活动名称")
    date.fromisoformat(values["event_date"])
    if event_id:
        current = detail(session, event_id)
        if current["revision"] != values.get("expected_revision"):
            raise ValueError("活动已变化，请刷新后重试")
        _event(principal, event_id, "attendance:update")
        payload = {"event_id": event_id, "name": title}
        if str(current["group"]["event_date"])[:10] != values["event_date"]:
            payload["event_date"] = values["event_date"]
        from datetime import datetime, timezone
        for field, clock in (("checkin_start_at", values["checkin_start"]), ("checkin_end_at", values["checkin_end"])):
            original = current["group"].get(field)
            if original:
                value = datetime.fromisoformat(values["event_date"] + "T" + clock + ":00+08:00")
                if datetime.fromisoformat(str(original).replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M") != value.strftime("%Y-%m-%dT%H:%M"):
                    payload[field] = value.isoformat()
        if {"event_date", "checkin_start_at", "checkin_end_at"} & set(payload) and not current["can_edit_times"]:
            raise ValueError("已有签到事实，不能修改日期或签到时间；仍可调整活动名称")
        _perform(principal, "event_update", payload)
        return {"id": event_id, "saved": True}
    _source_principal(principal, "attendance:create")
    if not _allowed(principal, "attendance:create", values["org_unit_id"]):
        raise PermissionError("活动不在创建权限的组织范围内")
    if values["checkin_start"] >= values["checkin_end"]:
        raise ValueError("签到截止时间必须晚于开放时间")
    activity_type = values.get("activity_type", "other")
    if activity_type == "class_meeting":
        class_id = values.get("class_org_unit_id")
        org = fetch_one("SELECT id,name FROM org_units WHERE id=? AND unit_type='CLASS' AND is_active=1", (class_id,))
        if not org or not _allowed(principal, "attendance:create", class_id):
            raise PermissionError("班级不在创建权限的组织范围内")
        from app.services.study_membership import group_roster
        with transaction() as connection:
            if not group_roster(connection, class_id=class_id, include_ungrouped=True):
                raise ValueError("当前班级没有有效在册名单，请先核对班级与小组管理资料")
        clocks = values["class_times"]
        for prefix in ("morning", "afternoon", "konpa"):
            if clocks[prefix + "_checkin_start"] >= clocks[prefix + "_checkin_end"] or clocks[prefix + "_scheduled_start"] >= clocks[prefix + "_scheduled_end"]:
                raise ValueError("每个场次的开始时间必须早于结束时间")
        payload = {"event_name": title, "event_date": values["event_date"], "org_unit_id": class_id,
            "class_org_unit_id": class_id, "class_name": org["name"], "group_field": "class_name",
            **{key: values["event_date"] + "T" + value for key, value in clocks.items()}}
        return _once(principal, "create_class_meeting_sessions", values["request_id"], payload, None)
    if activity_type == "group_meeting":
        raise ValueError("小组学习会请从小组学习会专用入口安排")
    payload = {"event_name": title, "event_date": values["event_date"], "activity_type": activity_type,
               "org_unit_id": values["org_unit_id"], "lifecycle_status": "DRAFT",
               "checkin_start_at": values["event_date"] + "T" + values["checkin_start"] + ":00+08:00",
               "checkin_end_at": values["event_date"] + "T" + values["checkin_end"] + ":00+08:00"}
    return _once(principal, "create_event", values["request_id"], payload, None)


def lifecycle(session, event_id, status):
    principal = _principal(session)
    writable()
    permission = "attendance:manage" if status == "CANCELLED" else "attendance:update"
    _event(principal, event_id, permission)
    # This operation selects its permission from the requested lifecycle status.
    from app.api.attendance_manage import perform
    scoped = _source_principal(principal, permission)
    with mobile_iam_context(scoped, permission):
        perform("event_lifecycle_update", {"event_id": event_id, "lifecycle_status": status}, {**scoped, "id": int(principal["user_id"])})
    return {"id": event_id, "saved": True}


def add_attendee(session, event_id, values):
    principal = _principal(session)
    writable()
    _event(principal, event_id, "attendance:manage")
    name = values["name"].strip()
    if not name:
        raise ValueError("请填写临时参加人员姓名")
    # Name/optional contact go through the existing identity resolver. Do not
    # manufacture a member linkage or a check-in fact from a client member ID.
    payload = {"event_id": event_id, "name": name, "phone": values.get("phone") or ""}
    return _once(principal, "registration", values["request_id"], payload, event_id)


def checkin_state(session, event_id, opened, expected_revision):
    principal = _principal(session)
    writable()
    current = detail(session, event_id)
    if current["revision"] != expected_revision:
        raise ValueError("场次状态已变化，请刷新后重试")
    _event(principal, event_id, "attendance:manage")
    if current["group"].get("status") == "CANCELLED":
        raise ValueError("活动已取消，不能调整签到开关")
    _perform(principal, "event_update", {"event_id": event_id, "status": "active" if opened else "closed"})
    return {"id": event_id, "saved": True}


def row_action(session, event_id, values):
    principal = _principal(session)
    writable()
    action = values["action"]
    operation = action if action in {"manual_checkin", "registration_delete"} else "attendance_status"
    _event(principal, event_id, signin_engine.OPERATIONS[operation])
    current = detail(session, event_id)
    row = next((row for row in current["attendees"] if str(row.get("registration_id")) == values["registration_id"]), None)
    if row is None:
        raise ValueError("报名不属于当前场次，请刷新后重试")
    checked = bool(row.get("checked") or row.get("checked_at"))
    if checked:
        if action == "manual_checkin":
            return {"id": event_id, "saved": True, "already_checked": True}
        raise ValueError("该学长已签到，保留签到事实，不能删除或修改跟进状态")
    if checked != values["expected_checked"] or values.get("expected_status") is not None and str(row.get("attendance_status") or "pending") != values["expected_status"]:
        raise ValueError("报名状态已变化，请刷新后核对")
    event = current["group"]
    if event.get("status") == "CANCELLED" or event.get("checkin_status") not in {"upcoming", "open"}:
        raise ValueError("当前场次未处于可维护的签到时段，请核对场次状态")
    if action == "manual_checkin":
        if row.get("is_team") or row.get("attendance_role") in {"EVENT_TEAM_MEMBER", "COURSE_TEAM_MEMBER"}:
            raise ValueError("团队名额请核实实际参加人后扫码签到，不能直接将联系人记为到场")
        if event.get("status") != "CONFIRMED" or event.get("checkin_status") != "open":
            raise ValueError("仅已确认且签到开放的场次支持人工签到")
    if action == "late" and str(event.get("session_code", "")).upper() == "KONPA":
        raise ValueError("空巴不设置预计迟到，请按实际到场签到")
    payload = {"event_id": event_id, "registration_id": values["registration_id"]}
    if operation == "attendance_status":
        payload.update(status=action, note=values.get("note") or "")
    return _once(principal, operation, values["request_id"], payload, event_id)


def sync_roster(session, event_id):
    principal = _principal(session)
    writable()
    _event(principal, event_id, "attendance:import")
    result = _perform(principal, "sync_class_roster", {"event_id": event_id})
    return {"id": event_id, "saved": True, "message": result.get("msg") or "名单已核对"}


def entry_code(session, event_id):
    writable()
    principal = _principal(session)
    from app.services.attendance_entry_codes import generate_entry
    scoped = _source_principal(principal, "attendance:code")
    with mobile_iam_context(scoped, "attendance:code"):
        result = generate_entry(event_id, user={**scoped, "id": int(principal["user_id"])}, env_version="release")
    return {key: result.get(key) for key in ("image_base64", "mime_type", "event_id", "page")}
