"""Scoped export context from live signin facts and canonical class/group rosters."""
from app.db import transaction
from app.services.attendance_registration_identity import attach_current_registration_names
from app.services.study_membership import group_roster


def enrich_export(data: dict, *, event_id: str, user: dict) -> None:
    from app.services import signin_engine as engine
    rows = data.get("rows")
    if not isinstance(rows, list):
        return
    event = data.get("event") or engine.event_detail(event_id, user=user, permission="attendance:export")
    data["event"] = event
    attach_current_registration_names(rows, event_id=event_id)
    allowed = engine.allowed_scope(user["id"], "attendance:export")

    def read(operation, payload, *, export=False):
        # Export authorization covers only this exact event/group's read context.
        # Never borrow the caller's union of other permission scopes.
        return engine.engine_request("/ops/v1/manage/" + operation, {
            "payload": payload, "actor": {"id": user["id"], "permissions": ["attendance:export" if export else "attendance:view"]},
            "allowed_org_unit_ids": allowed})

    data["stats"] = read("stats", {"event_id": event_id})
    if str(event.get("activity_type", "")).lower() != "class_meeting":
        return
    class_id = event.get("class_org_unit_id") or event.get("study_org_unit_id") or event.get("org_unit_id")
    group_id = event.get("event_group_id")
    day = str(event.get("event_date") or "")[:10]
    sessions, complete = {}, False
    if day:
        for page in range(1, 21):
            listing = read("admin_events", {"date_from": day, "date_to": day, "page": page, "page_size": 50})
            for item in listing.get("items", listing.get("events", [])):
                candidates = item.get("sessions") or [item]
                same_group = (bool(group_id) and str(item.get("event_group_id")) == str(group_id)) or any(str(s.get("event_id")) == event_id for s in candidates)
                if same_group:
                    for session in candidates:
                        merged = {**item, **session}
                        if (str(merged.get("session_code", "")).upper() in {"MORNING", "AM", "AFTERNOON", "PM"}
                                and merged.get("lifecycle_status") != "CANCELLED"):
                            sessions[str(merged["event_id"])] = merged
            if not listing.get("has_more"):
                complete = True
                break
    basis = "本次班会上午、下午本组在册学长去重到场人数 / 本组当前有效在册人数；跨班、来宾及空巴不混入本组参会人数。"
    if not complete or not sessions or not class_id:
        data.update(group_statistics=[], group_statistics_basis=basis, group_statistics_message="未取得完整的日间场次或班级关联，小组参会率待核对。")
        return
    daytime = []
    for session_id in sessions:
        if session_id == event_id:
            facts = rows
        else:
            # Check each source session against the export grant before reading it.
            sibling = engine.event_detail(session_id, user=user, permission="attendance:export")
            sibling_class = sibling.get("class_org_unit_id") or sibling.get("study_org_unit_id") or sibling.get("org_unit_id")
            if sibling_class != class_id or group_id and sibling.get("event_group_id") != group_id:
                raise engine.SigninEngineError("班会场次归属已变化，请刷新后重新导出", 409)
            facts = read("export", {"event_id": session_id}, export=True).get("rows", [])
            attach_current_registration_names(facts, event_id=session_id)
        daytime.extend(facts)
    with transaction() as connection:
        roster = group_roster(connection, class_id=class_id)
    members, groups = {}, {}
    for member in roster:
        members[int(member["member_id"])] = member["group_org_unit_id"]
        group = groups.setdefault(member["group_org_unit_id"], {"group_org_unit_id": member["group_org_unit_id"], "group_name": member["group_name"], "roster": set(), "present": set()})
        group["roster"].add(int(member["member_id"]))
    unresolved = 0
    for row in daytime:
        if not (row.get("checked") is True or row.get("checked_at")):
            continue
        if row.get("is_team") or row.get("team_id") or row.get("attendance_role") in {"GUEST", "NON_MEMBER"}:
            continue
        member_id = row.get("current_member_id")
        if member_id in members:
            groups[members[member_id]]["present"].add(member_id)
        elif not member_id and row.get("attendance_role") == "HOME_CLASS_MEMBER":
            unresolved += 1
    summary = []
    for group in sorted(groups.values(), key=lambda item: (item["group_name"], item["group_org_unit_id"])):
        total, present = len(group["roster"]), len(group["present"])
        summary.append({"group_org_unit_id": group["group_org_unit_id"], "group_name": group["group_name"],
                        "roster_count": total, "present_count": present,
                        "participation_rate": round(present / total, 6) if total and not unresolved else None})
    rates = {group["group_org_unit_id"]: group["participation_rate"] for group in summary}
    for row in rows:
        if row.get("current_class_org_unit_id") == class_id:
            row["class_group_participation_rate"] = rates.get(row.get("current_group_org_unit_id"))
    data.update(group_statistics=summary, group_statistics_basis=basis,
                group_statistics_message=f"本班有 {unresolved} 条已到场记录身份尚待核对，暂不展示参会率。" if unresolved else "",
                group_statistics_session_count=len(sessions))
