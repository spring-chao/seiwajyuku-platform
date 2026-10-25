"""Mobile facade to the existing authoritative signin management engine."""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

from app.db import execute, fetch_all, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids, mobile_iam_context
from app.services import signin_engine
from app.services.wechat_operations import _principal, _source_principal
from app.services.wechat_operations_v2 import writable


def _perform(principal, operation, values):
    from app.api.attendance_manage import perform
    permission = signin_engine.OPERATIONS[operation]
    scoped = _source_principal(principal, permission)
    with mobile_iam_context(scoped, permission):
        return perform(operation, values, {**scoped, "id": int(principal["user_id"])})


def _allowed(principal, permission, org_id):
    with mobile_iam_context(_source_principal(principal, permission), permission):
        allowed = accessible_org_ids(int(principal["user_id"]))
    return allowed is None or org_id in allowed


def context(session):
    principal = _principal(session)
    rows = fetch_all("SELECT id,name,unit_type FROM org_units WHERE is_active=1 ORDER BY name")
    return {"organizations": [row for row in rows if "attendance:create" in principal["permissions"]
                              and _allowed(principal, "attendance:create", row["id"])]}


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
        for event in row.get("sessions") or [row]:
            merged = {**row, **event}
            records.append(_normalized(merged))
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
                    for key, permission in (("can_update", "attendance:update"), ("can_manage", "attendance:manage"))}
    return {"group": _normalized(event), "attendees": data.get("rows", []), **capabilities,
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
        signin_engine.validate_organizations(payload, signin_engine.allowed_scope(actor, permission), required=operation == "create_event")
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
    receipt = {"id": str(result.get("event_id") or (result.get("data") or {}).get("event_id") or target or ""),
               "saved": True}
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
        _perform(principal, "event_update", payload)
        return {"id": event_id, "saved": True}
    _source_principal(principal, "attendance:create")
    if not _allowed(principal, "attendance:create", values["org_unit_id"]):
        raise PermissionError("活动不在创建权限的组织范围内")
    if values["checkin_start"] >= values["checkin_end"]:
        raise ValueError("签到截止时间必须晚于开放时间")
    payload = {"event_name": title, "event_date": values["event_date"], "activity_type": "other",
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
