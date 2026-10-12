"""Operator credit display and audited, ordinary CSV exports."""
from __future__ import annotations

import csv
import io
from contextlib import contextmanager

from app.db import transaction
from app.services.audit import write_audit
from app.services.iam import (accessible_org_ids, user_context, current_request_permission,
                              set_request_permission, clear_request_permission)
from app.services.learning_credits import dry_run_study_meeting_settlement
from app.services.study_meetings import (
    StudyMeetingPermissionError, get_study_meeting_record_for_operations,
    list_study_meeting_records,
)


@contextmanager
def permission_context(permission: str):
    previous = current_request_permission()
    set_request_permission(permission)
    try:
        yield
    finally:
        if previous is None:
            clear_request_permission()
        else:
            set_request_permission(previous)


def meeting_credit_preview(*, actor_user_id: int, session_id: int) -> dict:
    # Use the settlement calculator, including frozen rule versions, individual
    # completion facts and ledger deduplication. No second scoring algorithm.
    with permission_context("plans:credit_settlement_preview"):
        preview = dry_run_study_meeting_settlement(actor_user_id=actor_user_id, session_id=session_id)
        detail = get_study_meeting_record_for_operations(actor_user_id=actor_user_id, session_id=session_id)
    members = []
    for attendee in detail["attendees"]:
        entries = [item for item in preview["entries"] if item["member_id"] == attendee["member_id"]]
        group = sum(float(item["points"]) for item in entries if item["postable"] and item["entry_kind"] == "GROUP_MEETING_ATTENDANCE")
        course = sum(float(item["points"]) for item in entries if item["postable"] and item["entry_kind"] == "COURSE_COMPLETION")
        reasons = []
        for entry in entries:
            course_fact = next((course for course in detail["courses"] if course["course_key"] == entry.get("course_key")), {})
            fact = next((fact for fact in course_fact.get("completions", []) if fact["member_id"] == attendee["member_id"]), {})
            for reason in entry["reasons"]:
                reason = reason.removeprefix("RULE_MAPPING_MISSING:")
                if reason == "课程实际完成尚未确认" and fact.get("completion_status") == "NOT_COMPLETED":
                    reason = "未观看，不计课程学分"
                reasons.append(f"{entry['course_name']}：{reason}" if entry.get("course_name") else reason)
        members.append({
            "member_id": attendee["member_id"], "name": attendee["name"],
            "attendance_type": attendee["attendance_type"], "home_group_name": attendee["home_group_name"],
            "group_points": group, "course_points": course, "expected_points": group + course,
            "reasons": list(dict.fromkeys(reasons)),
        })
    return {"members": members, "totals": preview["totals"], "persisted": False}


def _csv_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def export_meeting_records(*, actor_user_id: int, session_id: int | None = None, **filters) -> str:
    user = user_context(actor_user_id) or {}
    if not {"exports:normal", "plans:credit_settlement_preview"}.issubset(user.get("permissions", [])):
        raise StudyMeetingPermissionError("需要普通导出和学分预览权限")
    export_scope = accessible_org_ids(actor_user_id, "exports:normal")
    with permission_context("plans:credit_settlement_preview"):
        if session_id is not None:
            records = [get_study_meeting_record_for_operations(actor_user_id=actor_user_id, session_id=session_id)]
            if export_scope is not None and records[0]["class_org_unit_id"] not in export_scope:
                raise StudyMeetingPermissionError("学习会不在导出授权范围内")
        else:
            records = list_study_meeting_records(actor_user_id=actor_user_id, **filters)
            records = [item for item in records if export_scope is None or item["class_org_unit_id"] in export_scope]
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["学习会编号", "日期", "班级", "小组", "周期", "状态", "学员编号", "姓名", "参加类型", "原小组", "课程", "观看确认", "预计小组会学分", "预计课程学分", "预计新增学分", "说明"])
        count = 0
        for record in records:
            detail = get_study_meeting_record_for_operations(actor_user_id=actor_user_id, session_id=record["id"])
            preview = meeting_credit_preview(actor_user_id=actor_user_id, session_id=record["id"])
            for member in preview["members"]:
                confirmations = []
                for course in detail["courses"]:
                    fact = next((fact for fact in course.get("completions", []) if fact["member_id"] == member["member_id"]), {})
                    label = {"CONFIRMED": "已观看", "NOT_COMPLETED": "未观看", "UNCONFIRMED": "待确认"}.get(fact.get("completion_status"), "待确认")
                    source = "人工确认" if fact.get("confirmed_by_user_id") else "登记信息"
                    confirmations.append(f"{course['course_name_snapshot']}：{label}（{source}）")
                writer.writerow([_csv_cell(value) for value in [
                    record["session_code"], record["meeting_date"], record["class_name"], record["group_name"], record["cycle_index"],
                    {"DRAFT": "草稿", "SUBMITTED": "已提交", "CANCELLED": "已取消"}[record["status"]],
                    member["member_id"], member["name"], "本组" if member["attendance_type"] == "HOME_GROUP" else "跨组", member["home_group_name"],
                    "；".join(course["course_name_snapshot"] for course in detail["courses"]), "；".join(confirmations),
                    member["group_points"], member["course_points"], member["expected_points"], "；".join(member["reasons"]) or "预计值，以正式结算为准",
                ]])
                count += 1
    with transaction() as connection:
        write_audit(connection, actor_user_id=actor_user_id, action="exports.study_meetings.normal",
                    resource_type="study_meeting_export", resource_id=str(session_id) if session_id is not None else None,
                    after={"session_count": len(records), "row_count": count, "filters": filters, "sensitive_fields": False})
    return "\ufeff" + output.getvalue()
