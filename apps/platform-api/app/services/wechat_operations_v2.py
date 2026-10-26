"""Staff mobile adapters. Each delegated call retains its own permission scope."""
from __future__ import annotations

from datetime import date

from app.core.settings import get_settings
from app.db import atomic_transaction, execute, fetch_all
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids, mobile_iam_context
from app.services.wechat_operations import _principal, _source_principal
from app.services import enrollment, learning_credits, study_meetings, renewals
from app.services.birthday_greetings import generate_birthday_greeting_draft
from app.services.plans import operations_snapshot


def call(principal, permission, function, *args, **kwargs):
    scoped = _source_principal(principal, permission)
    with mobile_iam_context(scoped, permission):
        return function(*args, **kwargs)


def writable():
    settings = get_settings()
    if settings.deployment_read_only or (settings.is_production and not settings.allow_production_mutations):
        raise ValueError("当前环境不允许写入")


def birthday_copy(session, member_id):
    principal = _principal(session)
    return call(principal, "members:detail_view", generate_birthday_greeting_draft,
                member_id, int(principal["user_id"]), selected_memory_ids=[], tone="warm")


def renewal_detail(session, cycle_id):
    principal = _principal(session)
    card = call(principal, "renewals:read", renewals.get_action_card, cycle_id, int(principal["user_id"]))
    # Return only the action and cycle, not support-network or care-note payloads.
    cycle = card["cycle"]
    allowed = False
    if "renewals:manage" in principal["permissions"]:
        try:
            call(principal, "renewals:manage", renewals.get_action_card, cycle_id, int(principal["user_id"]))
            allowed = True
        except PermissionError:
            pass
    name = card["member"]["name"]
    fallback = (f"{name}学长好，感谢您继续与盛和塾同行，期待接下来一起学习、实践与精进！"
                if cycle["status"] == "RENEWED" else f"{name}学长好，想关心一下您近期的学习和经营情况。如有需要支持的地方，欢迎随时告诉我们。")
    return {"cycle": cycle, "member": {"id": card["member"]["id"], "name": name}, "stage": card["stage"], "wechat_copy": card["action"]["wechat_reference"] or fallback,
            "can_manage": allowed}


def renewal_update(session, cycle_id, status, expected_status):
    principal = _principal(session)
    writable()
    with atomic_transaction() as connection:
        # Serialize status checks and updates, including MySQL's concurrent requests.
        execute(connection, "UPDATE renewal_cycles SET id=id WHERE id=?", (cycle_id,))
        row = execute(connection, "SELECT status FROM renewal_cycles WHERE id=?", (cycle_id,)).fetchone()
        call(principal, "renewals:manage", renewals.get_action_card, cycle_id, int(principal["user_id"]))
        if not row or row["status"] != expected_status:
            raise ValueError("续费状态已变化，请刷新后处理")
        call(principal, "renewals:manage", renewals.update_cycle, cycle_id, int(principal["user_id"]), status=status)
    return {"id": cycle_id, "status": status}


def _participation(record):
    from app.db import transaction
    from app.services.study_meeting_monthly_roster import roster_for_period
    from app.services.learning_requirement_policy import exemption_reason
    with transaction() as connection:
        roster,_ = roster_for_period(connection,str(record['meeting_date'])[:7])
        roster = [row for row in roster if row.get('group_org_unit_id')==record['study_group_org_unit_id'] and not exemption_reason(row.get('group_name'),row.get('class_name'),metric='group_meeting')]
    attendees = set(record.get('monthly_home_participant_ids', [int(row['member_id']) for row in record.get('attendees', [])]))
    return {"roster_count": record["home_group_member_count"],
            "home_present_count": record.get("monthly_home_present_count",record["current_home_count"]),
            "cross_group_count": record["current_cross_group_count"],
            "unresolved_count": record["current_unresolved_count"],
            "rate": record["home_group_attendance_rate"],
            "absent": [{"member_id": row["member_id"], "name": row["name"]} for row in roster if int(row["member_id"]) not in attendees],
            "basis": record.get("participation_basis","本人月度同班跨组参会计入本人小组"),
            "exempt_reason":record.get("requirement_exempt_reason","")}


def meetings(session, month):
    principal = _principal(session)
    start = date.fromisoformat(month + "-01")
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    rows = call(principal, "plans:read", study_meetings.list_study_meeting_records,
                actor_user_id=int(principal["user_id"]), meeting_date_from=start.isoformat(),
                meeting_date_to=date.fromordinal(end.toordinal() - 1).isoformat())
    for row in rows:
        row["participation"] = {"roster_count": row["home_group_member_count"],
                                "home_present_count": row.get("monthly_home_present_count",row["current_home_count"]),
                                "cross_group_count": row["current_cross_group_count"],
                                "unresolved_count": row["current_unresolved_count"],
                                "rate": row["home_group_attendance_rate"],"exempt_reason":row.get("requirement_exempt_reason","")}
    return rows


def meeting_detail(session, session_id):
    principal = _principal(session)
    actor = int(principal["user_id"])
    record = call(principal, "plans:read", study_meetings.get_study_meeting_record_for_operations,
                  actor_user_id=actor, session_id=session_id)
    preview = None
    if "plans:credit_settlement_preview" in principal["permissions"]:
        try:
            preview = call(principal, "plans:credit_settlement_preview", learning_credits.dry_run_study_meeting_settlement,
                           actor_user_id=actor, session_id=session_id)
        except PermissionError:
            pass
    if preview is not None:
        posted, elsewhere = [], []
        for item in preview["entries"]:
            entry = item.get("existing_entry")
            if entry:
                same_source = (str(entry.get("source_type")) == str(item.get("source_type"))
                               and str(entry.get("source_id")) == str(item.get("source_id")))
                (posted if same_source else elsewhere).append(entry)
                item["display_points"] = entry["points"]
                item["display_status"] = "本场已入账" if same_source else "本周期其他场次已计分"
            else:
                item["display_points"] = item["points"]
                item["display_status"] = {"READY": "待结算", "BLOCKED": "待处理", "NO_CREDIT": "规则不计分"}.get(item["status"], item["status"])
        preview["totals"].update(posted_points=sum(float(e["points"]) for e in posted),
            posted_entry_count=len(posted), credited_elsewhere_points=sum(float(e["points"]) for e in elsewhere),
            credited_elsewhere_count=len(elsewhere))
    can_confirm = False
    if "study_meetings:attendees_edit" in principal["permissions"]:
        try:
            editable = call(principal, "study_meetings:attendees_edit", study_meetings.get_study_meeting_record_for_operations,
                            actor_user_id=actor, session_id=session_id)
            can_confirm = editable["can_confirm_review"]
        except PermissionError:
            pass
    return {"record": record, "participation": _participation(record), "revision": record["review_fingerprint"],
            "preview": preview, "can_settle": can_confirm,
            "settlement_enabled": get_settings().learning_credit_settlement_enabled}


def verify_meeting(session, session_id, expected_revision):
    principal = _principal(session)
    writable()
    # Read scope is checked separately. The existing review service checks each
    # of attendees_edit, settlement_manage and settlement_post against its own
    # grant scope. Keep the verified principal for those explicit checks.
    call(principal, "plans:read", study_meetings.get_study_meeting_record_for_operations,
         actor_user_id=int(principal["user_id"]), session_id=session_id)
    _source_principal(principal, "study_meetings:attendees_edit")
    from app.services.study_meeting_review import confirm_and_settle_meeting_review
    with mobile_iam_context(principal, "study_meetings:attendees_edit"):
        result = confirm_and_settle_meeting_review(actor_user_id=int(principal["user_id"]),
                   session_id=session_id, expected_fingerprint=expected_revision)
    return {"review_status": result["review_status"], "credit_settlement": result["credit_settlement"]}


def dashboard(session, year, month):
    principal = _principal(session)
    snapshot = call(principal, "plans:read", operations_snapshot, user_id=int(principal["user_id"]), year=year, month=month)
    # Renewal read must have its own organization scope, never the plans grant scope.
    if "renewals:read" in principal["permissions"]:
        renewal_snapshot = call(principal, "renewals:read", operations_snapshot, user_id=int(principal["user_id"]), year=year, month=month)
        snapshot["summary"]["renewed_member_count"] = renewal_snapshot["summary"]["renewed_member_count"]
        snapshot["data_quality"]["renewal_source_authorized"] = True
        snapshot["data_quality"]["notes"].append("续费人数使用续费查看权限对应的组织范围。")
    else:
        snapshot["summary"]["renewed_member_count"] = None
    return snapshot


def applications(session, query=None, application_status=None):
    principal = _principal(session)
    rows = call(principal, "enrollment:read", enrollment.list_enrollment_applications,
                int(principal["user_id"]), query=query, application_status=application_status, limit=200)
    for row in rows:
        row.pop("phone", None)
    return rows


def application_detail(session, application_id):
    principal = _principal(session)
    detail = call(principal, "enrollment:read", enrollment.get_enrollment_application, int(principal["user_id"]), application_id)
    detail.pop("phone", None)
    actions = {}
    organizations = []
    for key, permission in (("review", "enrollment:review"), ("payment", "enrollment:payment_confirm"), ("enroll", "enrollment:enroll"), ("export", "exports:normal")):
        actions[key] = False
        if permission in principal["permissions"]:
            try:
                call(principal, permission, enrollment.get_enrollment_application, int(principal["user_id"]), application_id)
                actions[key] = True
            except PermissionError:
                pass
    if actions["review"]:
        with mobile_iam_context(_source_principal(principal, "enrollment:review"), "enrollment:review"):
            allowed = accessible_org_ids(int(principal["user_id"]))
            organizations = [row for row in fetch_all("SELECT id,name FROM org_units WHERE is_active=1 ORDER BY name")
                             if allowed is None or row["id"] in allowed]
    if actions["review"]:
        detail = call(principal, "enrollment:review", enrollment.get_enrollment_application,
                      int(principal["user_id"]), application_id, for_visit_export=True)
        detail.pop("phone", None)
    actions["visit_export"] = actions["review"]
    return {"application": detail, "actions": actions, "organizations": organizations}


def application_image(session, application_id):
    principal = _principal(session)
    actor = int(principal["user_id"])
    detail = call(principal, "enrollment:read", enrollment.get_enrollment_application, actor, application_id)
    call(principal, "exports:normal", enrollment.get_enrollment_application, actor, application_id)
    fields = ("application_no", "name", "phone_masked", "gender", "birthday", "district", "political_status", "social_role", "email", "referrer", "referrer_org_unit_name", "target_shuku_name", "org_unit_name", "join_date", "company_name", "position", "industry_category", "industry_other", "industry", "company_address", "company_products", "employee_count", "annual_sales", "profit_margin", "financial_fields_visible", "has_enterprise_financial_data", "invoice_type", "invoice_title", "invoice_tax_id", "company_tax_id", "invoice_registered_address", "invoice_phone", "invoice_bank", "invoice_account", "invoice_info", "goal_years", "revenue_growth_target", "profit_growth_target", "books_read", "enrollment_reason_philosophy", "enrollment_reason_change", "enrollment_reason_other", "learning_years_goal", "learning_participation_goal", "business_goal", "other_goal", "notes", "review_note", "application_status", "payment_status", "rules_acknowledged", "created_at", "reviewed_at", "payment_confirmed_at", "converted_at")
    from app.db import transaction
    with transaction() as connection:
        write_audit(connection, actor_user_id=actor, action="enrollment.mobile_summary_export",
                    resource_type="member_enrollment_application", resource_id=str(application_id),
                    org_unit_id=detail.get("org_unit_id"), after={"fields": list(fields), "format": "IMAGE_SUMMARY"})
    return {key: detail.get(key) for key in fields}


def application_visit_image(session, application_id, recipient, confirmed):
    principal = _principal(session)
    return call(principal, "enrollment:review", enrollment.export_enrollment_visit_data,
                int(principal["user_id"]), application_id, recipient=recipient, confirmed=confirmed)


def application_action(session, application_id, action, values, expected_updated_at):
    principal = _principal(session)
    writable()
    actor = int(principal["user_id"])
    permission = {"review": "enrollment:review", "payment": "enrollment:payment_confirm",
                  "reject": "enrollment:review", "enroll": "enrollment:enroll"}.get(action)
    if not permission:
        raise ValueError("未知申请操作")
    with atomic_transaction() as connection:
        execute(connection, "UPDATE member_enrollment_applications SET id=id WHERE id=?", (application_id,))
        current = call(principal, permission, enrollment.get_enrollment_application, actor, application_id)
        if str(current["updated_at"]) != expected_updated_at:
            raise ValueError("申请已被修改，请刷新后重新核对")
        if action == "review":
            call(principal, permission, enrollment.review_enrollment_application, actor, application_id,
                 decision=values["decision"], updates=values.get("updates", {}), review_note=values.get("note"))
        elif action == "payment":
            call(principal, permission, enrollment.confirm_enrollment_payment, actor, application_id,
                 payment_status="PAID", amount=values.get("amount"), note=values.get("note"))
        elif action == "reject":
            call(principal, permission, enrollment.reject_enrollment_application, actor, application_id, values["reason"])
        elif action == "enroll":
            call(principal, permission, enrollment.enroll_application, actor, application_id)
    return {"id": application_id, "saved": True}
