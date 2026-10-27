"""Read-only live class statistics for currently appointed class-team volunteers."""
from datetime import UTC, date, datetime, timedelta

from app.core.business_time import business_today
from app.db import fetch_one
from app.services import signin_engine as engine
from app.services.attendance_export_context import BASIS, scoped_participation_statistics
from app.services.volunteer_positions import CLASS_ATTENDANCE_VIEW, get_member_volunteer_services


def class_scopes(member_id):
    services = get_member_volunteer_services(member_id)
    if services.get("needs_manual_review"):
        raise PermissionError("当前志工任职范围待核对，请联系运营专员确认")
    classes = {}
    for role in services.get("roles", []):
        if CLASS_ATTENDANCE_VIEW not in role.get("capabilities", []):
            continue
        org_id = role.get("scope_org_unit_id")
        # Group posts serve their group's parent class, never the member's study class.
        org = fetch_one(
            "SELECT c.id,c.name FROM org_units target JOIN org_units c ON "
            "((target.unit_type='CLASS' AND c.id=target.id) OR "
            "(target.unit_type='GROUP' AND c.id=target.parent_id)) "
            "WHERE target.id=? AND target.is_active=1 AND c.unit_type='CLASS' AND c.is_active=1",
            (org_id,),
        )
        if org:
            classes[org["id"]] = dict(org)
    if not classes:
        raise PermissionError("当前没有可查看班会签到的有效班级志工任职")
    return sorted(classes.values(), key=lambda item: (item["name"], item["id"]))


def context(member_id):
    classes = class_scopes(member_id)
    today = business_today(datetime.now(UTC)).isoformat()
    return {"classes": classes, "today": today, "month": today[:7]}


def _reader(member_id, allowed):
    engine.enabled()
    cache = {}
    def read(operation, payload):
        if operation not in {"admin_events", "event_detail", "stats"}:
            raise PermissionError("志工签到服务仅可查看统计")
        key = (operation, tuple(sorted(payload.items())))
        if key not in cache:
            cache[key] = engine.engine_request("/ops/v1/manage/" + operation, {
                "payload": payload,
                "actor": {"id": f"volunteer-member:{member_id}", "permissions": ["attendance:view"]},
                "allowed_org_unit_ids": sorted(allowed)})
        return cache[key]
    return read


def _validate(event, allowed):
    if not isinstance(event, dict):
        raise engine.SigninEngineError("班会不存在", 404)
    class_id = event.get("class_org_unit_id") or event.get("study_org_unit_id") or event.get("org_unit_id")
    if (class_id not in allowed or event.get("group_org_unit_id")
            or str(event.get("activity_type", "")).lower() != "class_meeting"
            or event.get("lifecycle_status") != "CONFIRMED"):
        raise PermissionError("该班会不在本人当前服务班级范围内或尚未确认")
    engine.validate_organizations(event, allowed, required=True)
    return class_id


def _event_payload(event):
    return {key: event.get(key) for key in ("event_id", "event_group_id", "name", "event_date",
        "class_org_unit_id", "class_name", "session_code", "session_name", "checkin_status")}


def meetings(member_id, *, month, day=None, class_id=None, page=1):
    classes = class_scopes(member_id)
    allowed = {row["id"] for row in classes}
    if class_id:
        if class_id not in allowed:
            raise PermissionError("只能查看本人当前服务的班级")
        allowed = {class_id}
    start = date.fromisoformat(month + "-01")
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1) - timedelta(days=1)
    if not 2020 <= start.year <= 2100:
        raise ValueError("请选择有效的查询月份")
    if day:
        selected = date.fromisoformat(day)
        if not start <= selected <= end:
            raise ValueError("查询日期与月份不一致")
        start = end = selected
    read = _reader(member_id, allowed)
    data = read("admin_events", {"date_from": start.isoformat(), "date_to": end.isoformat(), "page": page, "page_size": 20})
    records = []
    for item in data.get("items", data.get("events", [])):
        candidates = item.get("sessions") or [item]
        valid = []
        for candidate in candidates:
            event = {**item, **candidate}
            try:
                _validate(event, allowed)
                event_day = date.fromisoformat(str(event.get("event_date") or "")[:10])
            except (PermissionError, ValueError, engine.SigninEngineError):
                continue
            if start <= event_day <= end:
                valid.append(event)
        if valid:
            # One picker item represents a class meeting, never three duplicate scenes.
            event = next((e for e in valid if str(e.get("session_code", "")).upper() in {"MORNING", "AM"}), valid[0])
            records.append({**_event_payload(event), "name": item.get("logical_name") or item.get("name") or event.get("name") or "班级学习会"})
    return {"records": records, "page": page, "has_more": bool(data.get("has_more"))}


def snapshot(member_id, event_id):
    allowed = {row["id"] for row in class_scopes(member_id)}
    read = _reader(member_id, allowed)
    selected = read("event_detail", {"event_id": event_id})
    event = selected.get("event")
    selected_class = _validate(event, allowed)
    if str(event.get("event_id")) != event_id:
        raise engine.SigninEngineError("班会信息已变化，请刷新后重试", 409)
    # Revalidate every sibling scene before the common calculator reads its facts.
    def scoped_read(operation, payload):
        data = read(operation, payload)
        if operation in {"event_detail", "stats"}:
            source = data.get("event")
            source_class = _validate(source, allowed)
            if (str(source.get("event_id")) != str(payload.get("event_id")) or source_class != selected_class
                    or source.get("event_group_id") != event.get("event_group_id")
                    or str(source.get("event_date"))[:10] != str(event.get("event_date"))[:10]):
                raise engine.SigninEngineError("班会场次归属已变化，请刷新后重试", 409)
        return data
    statistics = scoped_participation_statistics(event_id=event_id, allowed=sorted(allowed), read=scoped_read)
    if not statistics.get("class_statistics"):
        raise engine.SigninEngineError(statistics.get("group_statistics_message") or "统计资料尚未完整，请稍后刷新", 503)
    scenes = [{"key": "overall", "name": "整体", "class_statistics": statistics["class_statistics"],
               "group_statistics": statistics["group_statistics"], "group_totals": statistics["group_totals"],
               "basis": BASIS, "onsite_total": None, "message": statistics.get("group_statistics_message") or ""}]
    candidates = selected.get("sessions") or [event]
    session_events = {str(s.get("event_id")): {**event, **s} for s in candidates if s.get("event_id")}
    labels = {"MORNING": ("morning", "上午"), "AM": ("morning", "上午"),
              "AFTERNOON": ("afternoon", "下午"), "PM": ("afternoon", "下午"),
              "KONPA": ("konpa", "空巴"), "EVENING": ("konpa", "空巴")}
    for row in statistics.get("class_session_statistics", []):
        session_event = session_events.get(row["event_id"])
        if not session_event:
            session_event = scoped_read("event_detail", {"event_id": row["event_id"]})["event"]
        code = str(session_event.get("session_code") or "").upper()
        if code not in labels:
            continue
        raw = scoped_read("stats", {"event_id": row["event_id"]})
        key, label = labels[code]
        scenes.append({"key": key, "name": label,
                       "class_statistics": {k: row.get(k) for k in ("roster_count", "present_count", "participation_rate", "member_present_count", "staff_present_count", "member_participation_rate")},
                       "group_statistics": row["group_statistics"], "group_totals": row["group_totals"],
                       "onsite_total": raw.get("onsite_total", raw.get("checked")),
                       "message": "本场存在待核对身份，参会率暂不展示。" if row.get("unresolved_count") else "",
                       "basis": "仅统计本场次。本班精进组免要求；现场总人数保留外班、来宾等实际签到。"})
    order = {"overall": 0, "morning": 1, "afternoon": 2, "konpa": 3}
    return {"event": _event_payload(event), "scenes": sorted(scenes, key=lambda s: order[s["key"]]),
            "message": statistics.get("group_statistics_message") or "", "updated_at": datetime.now(UTC).isoformat()}
