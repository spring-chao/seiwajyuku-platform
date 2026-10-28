"""Shared scoped daytime participation for the screen and workbook."""
import re
from app.db import transaction
from app.services.attendance_registration_identity import attach_current_registration_names, registration_identities
from app.services.learning_requirement_policy import requirement_roster, excluded_members

BASIS = "上午、下午合并去重，空巴、来宾及团队名额排除。整体参会率计入本班在册学长及正式服务本班的班主任、副班主任、辅导员，分母为本班在册人数；小组及学长参会率只计在册学长，未分组计入班级。各班精进组不计入任何参会率分子或分母，参会事实仍保留。"
TEAM_ROLES = {"EVENT_TEAM_MEMBER", "COURSE_TEAM_MEMBER", "GUEST", "NON_MEMBER"}


def _hydrate(rows, event_id):
    missing = [r for r in rows if not r.get("platform_member_id") and not r.get("member_code")]
    for row, match in zip(missing, registration_identities(missing)):
        if match:
            row.update(match)
    attach_current_registration_names(rows, event_id=event_id)
    # Overlay audited platform makeups after the caller has authorized this
    # exact source session. All live participation and workbook readers use
    # this same effective arrival fact.
    from app.db import fetch_all
    makeups = fetch_all(
        "SELECT r.external_registration_id, r.external_record_id, r.member_id, r.checked_at "
        "FROM attendance_records r JOIN attendance_sessions s ON s.id=r.attendance_session_id "
        "JOIN attendance_event_groups eg ON eg.id=s.event_group_id "
        "WHERE eg.source_key='signin' AND s.external_session_id=? "
        "AND r.checkin_source='STAFF_MAKEUP' AND r.attendance_status='MANUAL_PRESENT'", (str(event_id),))
    for row in rows:
        matches = [item for item in makeups if
            (item['external_registration_id'] and str(item['external_registration_id']) == str(row.get('registration_id') or '')) or
            (item['external_record_id'] and str(item['external_record_id']) == str(row.get('record_id') or '')) or
            (item['member_id'] and str(item['member_id']) == str(row.get('current_member_id') or row.get('platform_member_id') or ''))]
        if len(matches) == 1:
            row['_staff_makeup_added'] = not (row.get('checked') or row.get('checked_at') or row.get('sign_time'))
            row.update(checked=True, checked_at=str(matches[0]['checked_at']), sign_time=str(matches[0]['checked_at']),
                       sign_status='人工补签', checkin_source='STAFF_MAKEUP')


def _class_id(event):
    return event.get("class_org_unit_id") or event.get("study_org_unit_id") or event.get("org_unit_id")


def _sort_group(group):
    return (group["group_org_unit_id"] is None,
            tuple((0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"(\d+)", group["group_name"])))


def participation_statistics(*, event_id, user, permission):
    from app.services import signin_engine as engine
    allowed = engine.allowed_scope(user["id"], permission)

    def read(operation, payload):
        return engine.engine_request("/ops/v1/manage/" + operation, {
            "payload": payload, "actor": {"id": user["id"], "permissions": ["attendance:view"]},
            "allowed_org_unit_ids": allowed})

    return scoped_participation_statistics(event_id=event_id, allowed=allowed, read=read)


def scoped_participation_statistics(*, event_id, allowed, read):
    """Shared calculation; callers must supply a server-verified scope reader.

    Employee callers resolve IAM grants above. Volunteer callers resolve live
    class appointments and expose only aggregates, without backend IAM grants.
    """
    from app.services import signin_engine as engine

    def detail(session_id):
        result = read("event_detail", {"event_id": session_id})
        event = result.get("event")
        if not isinstance(event, dict):
            raise engine.SigninEngineError("活动不存在", 404)
        engine.validate_organizations(event, allowed, required=True)
        return result

    selected = detail(event_id)
    event = selected["event"]
    if str(event.get("activity_type", "")).lower() != "class_meeting":
        return {}
    class_id, group_id = _class_id(event), event.get("event_group_id")
    day = str(event.get("event_date") or "")[:10]
    candidates = selected.get("sessions")
    complete = isinstance(candidates, list) and bool(candidates)
    if not complete:
        candidates = []
        if day:
            for page in range(1, 21):
                listing = read("admin_events", {"date_from": day, "date_to": day, "page": page, "page_size": 50})
                for item in listing.get("items", listing.get("events", [])):
                    sessions = item.get("sessions") or [item]
                    if (group_id and str(item.get("event_group_id")) == str(group_id)) or any(str(s.get("event_id")) == event_id for s in sessions):
                        candidates.extend({**item, **s} for s in sessions)
                if not listing.get("has_more"):
                    complete = True
                    break
    sessions = {str(s["event_id"]): s for s in candidates
                if s.get("event_id") and str(s.get("session_code", "")).upper() in {"MORNING", "AM", "AFTERNOON", "PM"}
                and s.get("lifecycle_status") != "CANCELLED"}
    result = {"group_statistics": [], "class_statistics": None, "group_totals": None,
              "group_statistics_basis": BASIS, "group_statistics_message": ""}
    if not complete or not sessions or not class_id or not day:
        result["group_statistics_message"] = "未取得完整日间场次或班级关联，参会率待核对。"
        return result
    metrics_sessions = dict(sessions)
    for candidate in candidates:
        if candidate.get('event_id') and str(candidate.get('session_code','')).upper() in {'KONPA','EVENING'} and candidate.get('lifecycle_status') != 'CANCELLED':
            metrics_sessions[str(candidate['event_id'])] = candidate
    facts, session_facts = [], {}
    for session_id in metrics_sessions:
        source = selected if session_id == event_id else detail(session_id)
        source_event = source["event"]
        if (_class_id(source_event) != class_id or group_id and source_event.get("event_group_id") != group_id
                or str(source_event.get("event_date") or "")[:10] != day):
            raise engine.SigninEngineError("班会场次归属已变化，请刷新后重试", 409)
        rows = source.get("rows")
        if not isinstance(rows, list):
            result["group_statistics_message"] = "班会场次名单尚未完整返回，参会率待核对。"
            return result
        _hydrate(rows, session_id)
        session_facts[session_id] = rows
        if session_id in sessions:
            facts.extend(rows)
    with transaction() as connection:
        roster = requirement_roster(connection, metric="class_meeting", class_id=class_id, include_ungrouped=True)
        from app.services.class_activity_participation import _class_staff
        staff_ids = set(_class_staff(connection, [class_id]).get(class_id, {}))
        staff_ids -= set(excluded_members(connection, staff_ids, metric="class_meeting"))
    members, groups = {}, {}
    for member in roster:
        member_id, key = int(member["member_id"]), member["group_org_unit_id"]
        members[member_id] = key
        group = groups.setdefault(key, {"group_org_unit_id": key,
            "group_name": member["group_name"] or "未分组", "roster": set(), "present": set()})
        group["roster"].add(member_id)
    unresolved = 0
    extra_staff = set()
    for row in facts:
        if not (row.get("checked") is True or row.get("checked_at") or row.get("sign_time")):
            continue
        if row.get("is_team") or row.get("team_id") or row.get("attendance_role") in TEAM_ROLES:
            continue
        member_id = row.get("current_member_id")
        if member_id in members:
            groups[members[member_id]]["present"].add(member_id)
        elif member_id in staff_ids:
            extra_staff.add(member_id)
        elif not member_id and row.get("attendance_role") in {"HOME_CLASS_MEMBER", "CROSS_CLASS_MEMBER"}:
            unresolved += 1
    def counts(total, present):
        return {"roster_count": total, "present_count": present,
                "participation_rate": round(present / total, 6) if total and not unresolved else None}
    summary = [{"group_org_unit_id": g["group_org_unit_id"], "group_name": g["group_name"],
                **counts(len(g["roster"]), len(g["present"]))} for g in sorted(groups.values(), key=_sort_group)]
    grouped = [g for g in groups.values() if g["group_org_unit_id"] is not None]
    per_session = []
    for source_id, rows in session_facts.items():
        known, missing = set(), 0
        for row in rows:
            if not (row.get('checked') is True or row.get('checked_at') or row.get('sign_time')) or row.get('is_team') or row.get('team_id') or row.get('attendance_role') in TEAM_ROLES:
                continue
            mid = row.get('current_member_id')
            if mid:known.add(mid)
            elif row.get('attendance_role') in {'HOME_CLASS_MEMBER','CROSS_CLASS_MEMBER'}:missing += 1
        student_count = len(known & set(members))
        staff_count = len((known & staff_ids) - set(members))
        def scene_counts(total, present):
            return {"roster_count": total, "present_count": present,
                    "participation_rate": round(present / total, 6) if total and not missing else None}
        scene_groups = [{"group_org_unit_id": g["group_org_unit_id"], "group_name": g["group_name"],
                         **scene_counts(len(g["roster"]), len(known & g["roster"]))}
                        for g in sorted(groups.values(), key=_sort_group)]
        per_session.append({'event_id':source_id,'session_code':metrics_sessions[source_id].get('session_code'),
            'session_name':metrics_sessions[source_id].get('session_name'),'includes_formal_class_staff':True,
            'roster_count':len(members),'member_present_count':student_count,'staff_present_count':staff_count,
            'present_count':student_count+staff_count,'unresolved_count':missing,
            'participation_rate':round((student_count+staff_count)/len(members),6) if members and not missing else None,
            'member_participation_rate':round(student_count/len(members),6) if members and not missing else None,
            'group_statistics':scene_groups,
            'group_totals':scene_counts(sum(len(g['roster']) for g in grouped),sum(len(known & g['roster']) for g in grouped))})
    result.update(group_statistics=summary, class_session_statistics=per_session,
        class_statistics={"class_org_unit_id": class_id,
            "class_name": next((r["class_name"] for r in roster), event.get("class_name") or "班级"),
            **counts(len(members), sum(len(g["present"]) for g in groups.values()) + len(extra_staff)),
            "member_present_count": sum(len(g["present"]) for g in groups.values()),
            "member_participation_rate": counts(len(members), sum(len(g["present"]) for g in groups.values()))["participation_rate"],
            "staff_present_count": len(extra_staff), "includes_formal_class_staff": True,
            "event_date": day},
        group_totals=counts(sum(len(g["roster"]) for g in grouped), sum(len(g["present"]) for g in grouped)),
        group_statistics_session_count=len(sessions), unresolved_record_count=unresolved, statistics_source="LIVE_SIGNIN",
        group_statistics_message=f"本班有 {unresolved} 条到场身份待核对，参会率暂不展示。" if unresolved else "")
    return result


def enrich_statistics(data, *, event_id, user):
    if str((data.get("event") or {}).get("activity_type", "")).lower() == "class_meeting":
        from app.services.signin_engine import SigninEngineError
        try:
            result = participation_statistics(event_id=event_id, user=user, permission="attendance:view")
            data.update(result)
            apply_session_requirements(data,result,event_id)
        except SigninEngineError as exc:
            if exc.status_code not in {502, 503}:
                raise
            data.update(group_statistics=[], class_statistics=None, group_totals=None,
                group_statistics_basis=BASIS, group_statistics_message="班会去重统计暂不可用，请稍后刷新；当前场次指标照常显示。")


def enrich_export(data, *, event_id, user):
    from app.services import signin_engine as engine
    rows = data.get("rows")
    if not isinstance(rows, list):
        return
    event = data.get("event") or engine.event_detail(event_id, user=user, permission="attendance:export")
    data["event"] = event
    _hydrate(rows, event_id)
    data["stats"] = engine.engine_request("/ops/v1/manage/stats", {
        "payload": {"event_id": event_id}, "actor": {"id": user["id"], "permissions": ["attendance:view"]},
        "allowed_org_unit_ids": engine.allowed_scope(user["id"], "attendance:export")})
    if str(event.get("activity_type", "")).lower() != "class_meeting":
        return
    data.update(participation_statistics(event_id=event_id, user=user, permission="attendance:export"))
    rates = {g["group_org_unit_id"]: g["participation_rate"] for g in data["group_statistics"]}
    for row in rows:
        if row.get("current_class_org_unit_id") == _class_id(event):
            row["class_group_participation_rate"] = rates.get(row.get("current_group_org_unit_id"))


def apply_session_requirements(stats,result,event_id):
    row = next((row for row in result.get('class_session_statistics',[]) if row['event_id']==event_id),None)
    if row is None:return
    stats['requirement_roster_count'] = row['roster_count']
    stats['requirement_member_present_count'] = row['member_present_count']
    stats['requirement_present_count'] = row['present_count']
    stats['requirement_participation_rate'] = row['participation_rate']
    stats['requirement_basis'] = '精进组不计入应参与和参与率，现场真人数仍保留其自愿参加事实'
