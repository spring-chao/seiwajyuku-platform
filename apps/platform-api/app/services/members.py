from __future__ import annotations

import csv
import io
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.privacy import decrypt_text, encrypt_text, protected_phone
from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids, user_context


DIRECT_PROFILE_ROLES = {"system_admin", "operations_admin", "regional_manager", "class_counselor", "group_leader"}


def _as_utc(value: str | datetime) -> datetime:
    # SQLite returns timestamps as strings, while PyMySQL returns DATETIME
    # columns as datetime objects. Contact access must support both drivers.
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def can_access_member(
    member_id: int, primary_org_id: str, allowed: set[str] | None
) -> bool:
    if allowed is None:
        return True
    if primary_org_id in allowed:
        return True
    if not allowed:
        return False
    placeholders = ",".join("?" for _ in allowed)
    now = datetime.now(UTC).isoformat()
    relation = fetch_one(
        "SELECT 1 AS allowed FROM member_org_relations "
        f"WHERE member_id=? AND org_unit_id IN ({placeholders}) "
        "AND (valid_from IS NULL OR valid_from<=?) "
        "AND (valid_until IS NULL OR valid_until>=?) LIMIT 1",
        (member_id, *sorted(allowed), now, now),
    )
    return relation is not None


def create_member(
    actor_user_id: int,
    *,
    member_code: str | None,
    name: str,
    org_unit_id: str,
    development_org_unit_id: str | None,
    phone: str | None,
    company_name: str | None = None,
    gender: str | None = None,
    district: str | None = None,
    company_address: str | None = None,
    class_name: str | None = None,
    group_name: str | None = None,
    birthday: str | None = None,
    join_date: str | None = None,
    study_start_date: str | None = None,
    membership_years: float | None = None,
    renewal_month: str | None = None,
    status: str = "ACTIVE",
    position: str | None = None,
    referrer: str | None = None,
    referrer_center: str | None = None,
    industry_category: str | None = None,
    industry: str | None = None,
    company_products: str | None = None,
    annual_sales: str | None = None,
    company_size: str | None = None,
    profit_margin: str | None = None,
    notes: str | None = None,
) -> int:
    allowed = accessible_org_ids(actor_user_id)
    if allowed is not None and org_unit_id not in allowed:
        raise PermissionError("不能在授权组织之外创建学长")
    fields: dict[str, str | None] = (
        protected_phone(phone)
        if phone and phone.strip()
        else {
            "phone_ciphertext": None,
            "phone_hash": None,
            "phone_last4": None,
            "phone_masked": None,
        }
    )
    now = datetime.now(UTC).isoformat()
    member_code = (member_code or "").strip() or (
        f"MEM-{datetime.now(UTC):%Y%m%d%H%M%S}-{secrets.token_hex(2).upper()}"
    )
    financial_data = {
        key: value.strip()
        for key, value in {
            "annual_sales": annual_sales or "",
            "profit_margin": profit_margin or "",
        }.items()
        if value.strip()
    }
    financial_ciphertext = (
        encrypt_text(json.dumps(financial_data, ensure_ascii=False))
        if financial_data
        else None
    )
    with transaction() as connection:
        cursor = execute(
            connection,
            "INSERT INTO members(member_code, name, org_unit_id, development_org_unit_id, status, "
            "phone_ciphertext, phone_hash, phone_last4, phone_masked, company_name, "
            "gender, district, company_address, class_name, group_name, birthday, join_date, "
            "study_start_date, membership_years, renewal_month, position, referrer, "
            "referrer_center, industry_category, industry, company_products, company_size, notes, "
            "enterprise_financial_ciphertext, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                member_code, name, org_unit_id, development_org_unit_id, status,
                fields["phone_ciphertext"], fields["phone_hash"], fields["phone_last4"],
                fields["phone_masked"], company_name, gender, district, company_address,
                class_name, group_name, birthday, join_date, study_start_date,
                membership_years, renewal_month, position, referrer, referrer_center,
                industry_category, industry, company_products, company_size, notes,
                financial_ciphertext, now, now,
            ),
        )
        member_id = cursor.lastrowid
        relations: list[tuple[str, str, bool]] = [
            ("PRIMARY_REGION", org_unit_id, True)
        ]
        if development_org_unit_id:
            relations.append(
                ("DEVELOPMENT_RELATION", development_org_unit_id, True)
            )

        class_org_id: str | None = None
        if class_name and class_name.strip():
            class_matches = execute(
                connection,
                "SELECT id, unit_type FROM org_units "
                "WHERE is_active=1 AND name=? "
                "AND unit_type IN ('CLASS', 'SPECIAL_COHORT')",
                (class_name.strip(),),
            ).fetchall()
            if len(class_matches) == 1:
                class_org_id = class_matches[0]["id"]
                relation_type = (
                    "SPECIAL_COHORT"
                    if class_matches[0]["unit_type"] == "SPECIAL_COHORT"
                    else "STUDY_CLASS"
                )
                relations.append((relation_type, class_org_id, True))

        if group_name and group_name.strip():
            group_sql = (
                "SELECT id FROM org_units WHERE is_active=1 "
                "AND unit_type='GROUP' AND name=?"
            )
            group_params: tuple[Any, ...] = (group_name.strip(),)
            if class_org_id:
                group_sql += " AND parent_id=?"
                group_params += (class_org_id,)
            group_matches = execute(
                connection, group_sql, group_params
            ).fetchall()
            if len(group_matches) == 1:
                relations.append(("STUDY_GROUP", group_matches[0]["id"], True))

        for relation_type, relation_org_id, is_primary in relations:
            execute(
                connection,
                "INSERT INTO member_org_relations"
                "(member_id, org_unit_id, relation_type, is_primary, "
                "source_type, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'MEMBER_CREATE', ?, ?)",
                (
                    member_id,
                    relation_org_id,
                    relation_type,
                    1 if is_primary else 0,
                    now,
                    now,
                ),
            )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="members.create",
            resource_type="member",
            resource_id=str(member_id),
            org_unit_id=org_unit_id,
            after={
                "member_code": member_code,
                "name": name,
                "phone": fields["phone_masked"],
            },
        )
        return member_id


def list_members(user_id: int, org_unit_id: str | None = None) -> list[dict[str, Any]]:
    params: list[Any] = []
    sql = (
        "SELECT m.id, m.member_code, m.name, m.org_unit_id, o.name AS org_name, "
        "m.development_org_unit_id, m.status, m.phone_masked, m.phone_last4, "
        "m.company_name, m.gender, m.district, m.company_address, m.class_name, "
        "m.group_name, m.birthday, m.join_date, m.study_start_date, m.membership_years, "
        "m.renewal_month, m.position, m.referrer, m.referrer_center, "
        "m.industry_category, m.industry, m.company_products, m.company_size, m.notes, "
        "m.enterprise_stage, m.sensitivity_level "
        "FROM members m JOIN org_units o ON o.id=m.org_unit_id"
    )
    if org_unit_id:
        sql += " WHERE m.org_unit_id=?"
        params.append(org_unit_id)
    sql += " ORDER BY o.name, m.name"
    rows = fetch_all(sql, tuple(params))
    allowed = accessible_org_ids(user_id)
    if allowed is not None:
        if not allowed:
            return []
        placeholders = ",".join("?" for _ in allowed)
        now = datetime.now(UTC).isoformat()
        related_ids = {
            row["member_id"]
            for row in fetch_all(
                "SELECT DISTINCT member_id FROM member_org_relations "
                f"WHERE org_unit_id IN ({placeholders}) "
                "AND (valid_from IS NULL OR valid_from<=?) "
                "AND (valid_until IS NULL OR valid_until>=?)",
                (*sorted(allowed), now, now),
            )
        }
        rows = [
            row
            for row in rows
            if row["org_unit_id"] in allowed or row["id"] in related_ids
        ]
    return rows


def reveal_contact(
    *, member_id: int, task_id: int, actor_user_id: int, purpose: str, client_reference: str | None
) -> dict[str, str]:
    purpose = purpose.strip()
    if len(purpose) < 4:
        raise ValueError("必须填写本次联系用途")
    task = fetch_one(
        "SELECT id, member_id, org_unit_id, assigned_user_id, status, due_at FROM followup_tasks WHERE id=?",
        (task_id,),
    )
    if not task or task["member_id"] != member_id:
        raise PermissionError("联系任务与学长不匹配")
    now = datetime.now(UTC)
    if task["assigned_user_id"] != actor_user_id or task["status"] not in {"OPEN", "IN_PROGRESS"}:
        raise PermissionError("只有当前有效任务责任人可以查看")
    if task["due_at"] and _as_utc(task["due_at"]) < now:
        raise PermissionError("联系任务已过期")
    allowed = accessible_org_ids(actor_user_id)
    if allowed is not None and task["org_unit_id"] not in allowed:
        raise PermissionError("任务不在组织授权范围内")
    member = fetch_one(
        "SELECT id, name, phone_ciphertext, phone_masked FROM members WHERE id=?", (member_id,)
    )
    if not member or not member["phone_ciphertext"]:
        raise ValueError("学长没有可用联系方式")
    phone = decrypt_text(member["phone_ciphertext"])
    with transaction() as connection:
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="members.contact.reveal",
            resource_type="member",
            resource_id=str(member_id),
            org_unit_id=task["org_unit_id"],
            purpose=purpose,
            after={"task_id": task_id, "phone": member["phone_masked"]},
        )
    return {"name": member["name"], "phone": phone, "expires_in": "60秒"}


def get_member_detail(member_id: int, actor_user_id: int) -> dict[str, Any]:
    """返回学长基本资料，手机号脱敏，不含企业敏感财务数据。

    完整手机号须经过 reveal_contact（任务用途控制）。
    完整企业敏感资料须经过 get_member_enterprise_detail（用途+审计）。
    """
    user = user_context(actor_user_id)
    if not user or not DIRECT_PROFILE_ROLES.intersection(user["roles"]):
        raise PermissionError("当前角色不能查看学长资料")
    member = fetch_one(
        "SELECT m.id, m.name, m.org_unit_id, o.name AS org_name, "
        "m.phone_masked, m.phone_last4, "
        "m.gender, m.birthday, m.district, m.class_name, m.group_name, m.join_date, "
        "m.study_start_date, m.membership_years, m.renewal_month, m.status, m.position, "
        "m.referrer, m.referrer_center, m.company_name, m.company_address, "
        "m.industry_category, m.industry, m.company_products, m.company_size, m.notes "
        "FROM members m "
        "JOIN org_units o ON o.id=m.org_unit_id WHERE m.id=?",
        (member_id,),
    )
    if not member:
        raise ValueError("学长不存在")
    allowed = accessible_org_ids(actor_user_id)
    if not can_access_member(member_id, member["org_unit_id"], allowed):
        raise PermissionError("学长不在组织授权范围内")
    with transaction() as connection:
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="members.profile.view",
            resource_type="member",
            resource_id=str(member_id),
            org_unit_id=member["org_unit_id"],
            after={"fields": "basic_profile_masked"},
        )
    return dict(member)


def get_member_enterprise_detail(
    member_id: int, actor_user_id: int, purpose: str
) -> dict[str, Any]:
    """返回企业敏感资料，不返回完整手机号，必须填写用途并写审计。

    仅 operations_admin 和 system_admin 可调用。
    """
    purpose = purpose.strip()
    if len(purpose) < 4:
        raise ValueError("必须填写查看用途（至少4个字符）")
    user = user_context(actor_user_id)
    if not user or not {"system_admin", "operations_admin"}.intersection(user["roles"]):
        raise PermissionError("当前角色不能查看完整企业资料")
    member = fetch_one(
        "SELECT m.id, m.name, m.org_unit_id, o.name AS org_name, m.phone_masked, "
        "m.gender, m.birthday, m.district, m.class_name, m.group_name, m.join_date, "
        "m.study_start_date, m.membership_years, m.renewal_month, m.status, m.position, "
        "m.referrer, m.referrer_center, m.company_name, m.company_address, "
        "m.industry_category, m.industry, m.company_products, m.company_size, m.notes, "
        "m.enterprise_financial_ciphertext FROM members m "
        "JOIN org_units o ON o.id=m.org_unit_id WHERE m.id=?",
        (member_id,),
    )
    if not member:
        raise ValueError("学长不存在")
    allowed = accessible_org_ids(actor_user_id)
    if not can_access_member(member_id, member["org_unit_id"], allowed):
        raise PermissionError("学长不在组织授权范围内")
    financial_data = (
        json.loads(decrypt_text(member["enterprise_financial_ciphertext"]))
        if member["enterprise_financial_ciphertext"]
        else {}
    )
    with transaction() as connection:
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="members.profile.enterprise_view",
            resource_type="member",
            resource_id=str(member_id),
            org_unit_id=member["org_unit_id"],
            purpose=purpose,
            after={"fields": "full_member_and_enterprise_profile"},
        )
    return {
        key: value
        for key, value in {
            **member,
            "annual_sales": financial_data.get("annual_sales"),
            "profit_margin": financial_data.get("profit_margin"),
        }.items()
        if key != "enterprise_financial_ciphertext"
    }


def normal_export_csv(user_id: int) -> str:
    rows = list_members(user_id)
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["member_code", "name", "org_name", "phone_masked", "company_name", "status"],
    )
    writer.writeheader()
    writer.writerows(
        {key: row.get(key) for key in writer.fieldnames}
        for row in rows
    )
    with transaction() as connection:
        write_audit(
            connection,
            actor_user_id=user_id,
            action="exports.members.normal",
            resource_type="member_export",
            after={"row_count": len(rows), "sensitive_fields": False},
        )
    return "\ufeff" + output.getvalue()


def create_sensitive_export(user_id: int, purpose: str, second_confirmed: bool) -> int:
    purpose = purpose.strip()
    if not second_confirmed or len(purpose) < 6:
        raise ValueError("敏感导出必须填写用途并完成二次确认")
    user = user_context(user_id)
    if not user or "data_security_admin" not in user["roles"]:
        raise PermissionError("仅数据安全管理员可执行敏感导出")
    rows = list_members(user_id)
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["member_code", "name", "org_name", "phone", "company_name", "status"],
    )
    writer.writeheader()
    for row in rows:
        sensitive = fetch_one("SELECT phone_ciphertext FROM members WHERE id=?", (row["id"],))
        writer.writerow({
            "member_code": row["member_code"],
            "name": row["name"],
            "org_name": row["org_name"],
            "phone": decrypt_text(sensitive["phone_ciphertext"]) if sensitive["phone_ciphertext"] else "",
            "company_name": row.get("company_name"),
            "status": row["status"],
        })
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=15)
    watermark = f"敏感数据｜仅限{user['display_name']}｜{now.isoformat()}"
    payload = encrypt_text(watermark + "\n" + output.getvalue())
    with transaction() as connection:
        cursor = execute(
            connection,
            "INSERT INTO sensitive_export_jobs(actor_user_id, export_type, org_scope_json, fields_json, "
            "purpose, second_confirmed, watermark_text, payload_ciphertext, status, expires_at, created_at) "
            "VALUES (?, 'SENSITIVE', ?, ?, ?, 1, ?, ?, 'READY', ?, ?)",
            (
                user_id, json.dumps(user["scopes"], ensure_ascii=False),
                json.dumps(writer.fieldnames, ensure_ascii=False), purpose, watermark, payload,
                expires.isoformat(), now.isoformat(),
            ),
        )
        job_id = cursor.lastrowid
        write_audit(
            connection,
            actor_user_id=user_id,
            action="exports.members.sensitive.create",
            resource_type="sensitive_export_job",
            resource_id=str(job_id),
            purpose=purpose,
            after={"row_count": len(rows), "expires_at": expires.isoformat()},
        )
        return job_id


def download_sensitive_export(job_id: int, user_id: int) -> str:
    user = user_context(user_id)
    if not user or "data_security_admin" not in user["roles"]:
        raise PermissionError("仅数据安全管理员可下载敏感导出")
    job = fetch_one("SELECT * FROM sensitive_export_jobs WHERE id=?", (job_id,))
    if not job or job["actor_user_id"] != user_id or job["status"] != "READY":
        raise PermissionError("导出任务不可用")
    if _as_utc(job["expires_at"]) < datetime.now(UTC):
        raise PermissionError("导出链接已过期")
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO export_download_logs(export_job_id, actor_user_id, result, downloaded_at) "
            "VALUES (?, ?, 'SUCCESS', ?)",
            (job_id, user_id, datetime.now(UTC).isoformat()),
        )
    return decrypt_text(job["payload_ciphertext"])
