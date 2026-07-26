from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids, user_context


CHANNELS = {"PHONE", "WECHAT", "MEETING", "VISIT", "COURSE", "OTHER"}
OUTCOMES = {"CONNECTED", "NO_ANSWER", "DECLINED", "RESCHEDULED", "COMPLETED", "OTHER"}
OPEN_STATES = {"OPEN", "IN_PROGRESS"}


def list_assignees(actor_user_id: int, org_unit_id: str | None = None) -> list[dict[str, Any]]:
    actor_allowed = accessible_org_ids(actor_user_id)
    if org_unit_id and actor_allowed is not None and org_unit_id not in actor_allowed:
        raise PermissionError("组织不在当前用户授权范围内")
    rows = fetch_all(
        "SELECT id, username, display_name FROM app_users WHERE is_active=1 ORDER BY display_name, id"
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        context = user_context(row["id"])
        if not context or "followups:manage" not in context["permissions"]:
            continue
        if org_unit_id:
            assignee_allowed = accessible_org_ids(row["id"])
            if assignee_allowed is not None and org_unit_id not in assignee_allowed:
                continue
        result.append(row)
    return result


def _task_for_actor(task_id: int, user_id: int, *, assignee_required: bool = False) -> dict:
    task = fetch_one(
        "SELECT t.*, m.name AS member_name, m.phone_masked, m.company_name "
        "FROM followup_tasks t JOIN members m ON m.id=t.member_id WHERE t.id=?",
        (task_id,),
    )
    if not task:
        raise ValueError("跟进任务不存在")
    allowed = accessible_org_ids(user_id)
    if allowed is not None and task["org_unit_id"] not in allowed:
        raise PermissionError("跟进任务不在组织授权范围内")
    if assignee_required and task["assigned_user_id"] != user_id:
        raise PermissionError("只有当前任务责任人可以记录跟进结果")
    return task


def create_task(
    actor_user_id: int,
    *,
    member_id: int,
    task_type: str,
    service_purpose: str,
    assigned_user_id: int,
    due_at: str | None,
    confidentiality_level: str = "ASSIGNEE",
) -> int:
    member = fetch_one("SELECT id, org_unit_id, phone_masked FROM members WHERE id=?", (member_id,))
    if not member:
        raise ValueError("学长不存在")
    allowed = accessible_org_ids(actor_user_id)
    if allowed is not None and member["org_unit_id"] not in allowed:
        raise PermissionError("不能为组织授权范围外的学长创建任务")
    assignee_allowed = accessible_org_ids(assigned_user_id)
    if assignee_allowed is not None and member["org_unit_id"] not in assignee_allowed:
        raise ValueError("任务责任人没有该学长所属组织的数据权限")
    if confidentiality_level not in {"ASSIGNEE", "ORG_MANAGERS"}:
        raise ValueError("未知保密级别")
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        cursor = execute(
            connection,
            "INSERT INTO followup_tasks(member_id, org_unit_id, task_type, service_purpose, "
            "assigned_user_id, status, confidentiality_level, due_at, created_by, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?)",
            (
                member_id, member["org_unit_id"], task_type.strip().upper(),
                service_purpose.strip(), assigned_user_id, confidentiality_level,
                due_at, actor_user_id, now, now,
            ),
        )
        task_id = cursor.lastrowid
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="followups.task.create",
            resource_type="followup_task",
            resource_id=str(task_id),
            org_unit_id=member["org_unit_id"],
            after={
                "member_id": member_id,
                "phone": member["phone_masked"],
                "assigned_user_id": assigned_user_id,
                "due_at": due_at,
            },
        )
        return task_id


def list_tasks(user_id: int, status: str | None = None) -> list[dict[str, Any]]:
    rows = fetch_all(
        "SELECT t.id, t.member_id, m.name AS member_name, m.phone_masked, m.company_name, "
        "t.org_unit_id, o.name AS org_name, t.task_type, t.service_purpose, "
        "t.assigned_user_id, u.display_name AS assignee_name, t.status, "
        "t.confidentiality_level, t.due_at, t.next_followup_at, t.updated_at "
        "FROM followup_tasks t JOIN members m ON m.id=t.member_id "
        "JOIN org_units o ON o.id=t.org_unit_id "
        "JOIN app_users u ON u.id=t.assigned_user_id "
        + ("WHERE t.status=? " if status else "")
        + "ORDER BY CASE t.status WHEN 'OPEN' THEN 1 WHEN 'IN_PROGRESS' THEN 2 ELSE 3 END, "
        "t.due_at, t.id",
        (status,) if status else (),
    )
    allowed = accessible_org_ids(user_id)
    if allowed is not None:
        rows = [row for row in rows if row["org_unit_id"] in allowed]
    return [
        {**row, "can_record": row["assigned_user_id"] == user_id}
        for row in rows
        if row["confidentiality_level"] != "ASSIGNEE" or row["assigned_user_id"] == user_id
    ]


def add_followup_record(
    task_id: int,
    actor_user_id: int,
    *,
    channel: str,
    contacted_at: str,
    outcome_code: str,
    subject_statement: str | None,
    objective_facts: str | None,
    staff_judgment: str | None,
    next_action: str | None,
    next_followup_at: str | None,
) -> int:
    task = _task_for_actor(task_id, actor_user_id, assignee_required=True)
    if task["status"] not in OPEN_STATES:
        raise ValueError("已关闭任务不能追加跟进记录")
    channel = channel.upper()
    outcome_code = outcome_code.upper()
    if channel not in CHANNELS or outcome_code not in OUTCOMES:
        raise ValueError("未知跟进渠道或结果")
    if not any((subject_statement, objective_facts, staff_judgment)):
        raise ValueError("主观陈述、客观事实、工作人员判断至少填写一项")
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        cursor = execute(
            connection,
            "INSERT INTO followup_records(task_id, member_id, channel, contacted_at, "
            "subject_statement, objective_facts, staff_judgment, outcome_code, next_action, "
            "next_followup_at, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id, task["member_id"], channel, contacted_at, subject_statement,
                objective_facts, staff_judgment, outcome_code, next_action,
                next_followup_at, actor_user_id, now,
            ),
        )
        record_id = cursor.lastrowid
        execute(
            connection,
            "UPDATE followup_tasks SET status='IN_PROGRESS', next_followup_at=?, updated_at=? WHERE id=?",
            (next_followup_at, now, task_id),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="followups.record.create",
            resource_type="followup_task",
            resource_id=str(task_id),
            org_unit_id=task["org_unit_id"],
            after={"record_id": record_id, "channel": channel, "outcome_code": outcome_code},
        )
        return record_id


def add_visit_record(
    task_id: int,
    actor_user_id: int,
    *,
    appointment_at: str | None,
    visited_at: str,
    purpose: str,
    participants: list[str],
    location_type: str,
    objective_facts: str,
    expressed_needs: str | None,
    support_provided: str | None,
    staff_judgment: str | None,
    next_action: str | None,
    next_followup_at: str | None,
) -> int:
    task = _task_for_actor(task_id, actor_user_id, assignee_required=True)
    if task["status"] not in OPEN_STATES:
        raise ValueError("已关闭任务不能追加走访记录")
    if not purpose.strip() or not objective_facts.strip():
        raise ValueError("走访目的和客观事实必须填写")
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        cursor = execute(
            connection,
            "INSERT INTO enterprise_visit_records(task_id, member_id, appointment_at, visited_at, "
            "purpose, participants_json, location_type, objective_facts, expressed_needs, "
            "support_provided, staff_judgment, next_action, next_followup_at, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id, task["member_id"], appointment_at, visited_at, purpose.strip(),
                json.dumps(participants, ensure_ascii=False), location_type, objective_facts.strip(),
                expressed_needs, support_provided, staff_judgment, next_action,
                next_followup_at, actor_user_id, now,
            ),
        )
        visit_id = cursor.lastrowid
        execute(
            connection,
            "UPDATE followup_tasks SET status='IN_PROGRESS', next_followup_at=?, updated_at=? WHERE id=?",
            (next_followup_at, now, task_id),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="followups.visit.create",
            resource_type="followup_task",
            resource_id=str(task_id),
            org_unit_id=task["org_unit_id"],
            after={"visit_id": visit_id, "location_type": location_type},
        )
        return visit_id


def close_task(task_id: int, actor_user_id: int, closure_note: str) -> None:
    task = _task_for_actor(task_id, actor_user_id, assignee_required=True)
    if task["status"] not in OPEN_STATES:
        raise ValueError("任务已经关闭")
    count = fetch_one(
        "SELECT (SELECT COUNT(*) FROM followup_records WHERE task_id=?) + "
        "(SELECT COUNT(*) FROM enterprise_visit_records WHERE task_id=?) AS total",
        (task_id, task_id),
    )
    if not count or not count["total"]:
        raise ValueError("至少记录一次联系或走访结果后才能关闭任务")
    if len(closure_note.strip()) < 4:
        raise ValueError("请填写任务关闭说明")
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        execute(
            connection,
            "UPDATE followup_tasks SET status='CLOSED', next_followup_at=NULL, updated_at=? WHERE id=?",
            (now, task_id),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="followups.task.close",
            resource_type="followup_task",
            resource_id=str(task_id),
            org_unit_id=task["org_unit_id"],
            purpose=closure_note.strip(),
            after={"status": "CLOSED"},
        )
