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


def _as_utc(value: str | datetime) -> datetime:
    # SQLite returns timestamps as strings, while PyMySQL returns DATETIME
    # columns as datetime objects. Contact access must support both drivers.
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def can_access_member(
    member_id: int, primary_org_id: str, allowed: set[str] | None
) -> bool:
    try:
        resolve_member_scope(member_id, primary_org_id, allowed)
    except PermissionError:
        return False
    return True


def resolve_member_scope(
    member_id: int, primary_org_id: str, allowed: set[str] | None
) -> str:
    """Resolve the formal member relation used to scope a task or contact."""
    if allowed is None or primary_org_id in allowed:
        return primary_org_id
    if not allowed:
        raise PermissionError("学员不在组织授权范围内")
    placeholders = ",".join("?" for _ in allowed)
    now = datetime.now(UTC).isoformat()
    relation = fetch_one(
        "SELECT org_unit_id FROM member_org_relations "
        f"WHERE member_id=? AND org_unit_id IN ({placeholders}) "
        "AND (valid_from IS NULL OR valid_from<=?) "
        "AND (valid_until IS NULL OR valid_until>=?) "
        "ORDER BY CASE relation_type "
        "WHEN 'STUDY_GROUP' THEN 1 WHEN 'STUDY_CLASS' THEN 2 "
        "WHEN 'DEVELOPMENT_RELATION' THEN 3 ELSE 4 END, id LIMIT 1",
        (member_id, *sorted(allowed), now, now),
    )
    if not relation:
        raise PermissionError("学员不在组织授权范围内")
    return relation["org_unit_id"]


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
    class_org_unit_id: str | None = None,
    group_org_unit_id: str | None = None,
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
    primary_org = fetch_one(
        "SELECT unit_type, is_active FROM org_units WHERE id=?", (org_unit_id,)
    )
    if not primary_org or not primary_org["is_active"]:
        raise ValueError("所属分中心不存在或已停用")
    if development_org_unit_id:
        development_org = fetch_one(
            "SELECT unit_type, is_active FROM org_units WHERE id=?",
            (development_org_unit_id,),
        )
        if not development_org or not development_org["is_active"]:
            raise ValueError("发展组织不存在或已停用")
        if development_org["unit_type"] not in {"ROOT", "REGIONAL_CENTER"}:
            raise ValueError("发展组织必须是根节点或分中心")
        if allowed is not None and development_org_unit_id not in allowed:
            raise PermissionError("不能将学长关联到授权范围外的发展组织")
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
    if fields["phone_hash"]:
        duplicate = fetch_one(
            "SELECT member_code FROM members WHERE phone_hash=? LIMIT 1",
            (fields["phone_hash"],),
        )
        if duplicate:
            raise ValueError(
                f"手机号已存在学员档案（{duplicate['member_code']}），请先人工核对或执行档案合并"
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
    class_org_id: str | None = None
    if class_org_unit_id:
        class_org = fetch_one(
            "SELECT id, name, unit_type, parent_id, is_active FROM org_units WHERE id=?",
            (class_org_unit_id,),
        )
        if (
            not class_org
            or not class_org["is_active"]
            or class_org["unit_type"] not in {"CLASS", "SPECIAL_COHORT"}
        ):
            raise ValueError("班级组织不存在、已停用或类型不正确")
        if class_org["parent_id"] not in {org_unit_id, "org-suzhou"}:
            raise ValueError("班级不属于所选分中心")
        class_org_id = class_org["id"]
        class_name = class_org["name"]
    elif class_name and class_name.strip():
        class_matches = fetch_all(
            "SELECT id, name, unit_type, parent_id FROM org_units "
            "WHERE is_active=1 AND name=? AND unit_type IN ('CLASS', 'SPECIAL_COHORT')",
            (class_name.strip(),),
        )
        if len(class_matches) != 1:
            raise ValueError("班级文本无法唯一匹配正式组织，请改用班级组织ID")
        if class_matches[0]["parent_id"] not in {org_unit_id, "org-suzhou"}:
            raise ValueError("班级不属于所选分中心")
        class_org_id = class_matches[0]["id"]
        class_name = class_matches[0]["name"]
    group_org_id: str | None = None
    if group_org_unit_id:
        group_org = fetch_one(
            "SELECT id, name, unit_type, parent_id, is_active FROM org_units WHERE id=?",
            (group_org_unit_id,),
        )
        if (
            not group_org
            or not group_org["is_active"]
            or group_org["unit_type"] != "GROUP"
        ):
            raise ValueError("小组组织不存在、已停用或类型不正确")
        if not class_org_id:
            raise ValueError("小组必须同时选择所属班级")
        if group_org["parent_id"] != class_org_id:
            raise ValueError("小组不属于所选班级")
        group_org_id = group_org["id"]
        group_name = group_org["name"]
    elif group_name and group_name.strip():
        group_sql = (
            "SELECT id, name FROM org_units WHERE is_active=1 AND unit_type='GROUP' "
            "AND name=?"
        )
        group_params: tuple[Any, ...] = (group_name.strip(),)
        if not class_org_id:
            raise ValueError("小组必须同时选择所属班级")
        group_sql += " AND parent_id=?"
        group_params += (class_org_id,)
        group_matches = fetch_all(group_sql, group_params)
        if len(group_matches) != 1:
            raise ValueError("小组文本无法唯一匹配正式组织，请改用小组组织ID")
        group_org_id = group_matches[0]["id"]
        group_name = group_matches[0]["name"]
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

        if class_org_id:
            class_org = execute(
                connection, "SELECT unit_type FROM org_units WHERE id=?", (class_org_id,)
            ).fetchone()
            relation_type = (
                "SPECIAL_COHORT"
                if class_org["unit_type"] == "SPECIAL_COHORT"
                else "STUDY_CLASS"
            )
            relations.append((relation_type, class_org_id, True))
        if group_org_id:
            relations.append(("STUDY_GROUP", group_org_id, True))

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


def update_member(actor_user_id: int, member_id: int, updates: dict[str, Any]) -> int:
    """Update a member profile and its formal organization relations atomically."""
    current = fetch_one(
        "SELECT id, name, org_unit_id, development_org_unit_id, status, phone_masked, "
        "company_name, notes, class_name, group_name FROM members WHERE id=?",
        (member_id,),
    )
    if not current:
        raise ValueError("学员不存在")
    if not updates:
        raise ValueError("至少提供一项需要修改的字段")
    allowed_fields = {
        "name", "status", "phone", "company_name", "notes",
        "org_unit_id", "development_org_unit_id", "class_org_unit_id",
        "group_org_unit_id",
    }
    unknown = set(updates) - allowed_fields
    if unknown:
        raise ValueError(f"不支持修改字段：{','.join(sorted(unknown))}")
    allowed = accessible_org_ids(actor_user_id)
    try:
        resolve_member_scope(member_id, current["org_unit_id"], allowed)
    except PermissionError as exc:
        raise PermissionError("学员不在组织授权范围内") from exc

    target_org = updates.get("org_unit_id", current["org_unit_id"])
    target_org_row = fetch_one(
        "SELECT unit_type, is_active FROM org_units WHERE id=?", (target_org,)
    )
    if not target_org_row or not target_org_row["is_active"]:
        raise ValueError("所属分中心不存在或已停用")
    if allowed is not None and target_org not in allowed:
        raise PermissionError("不能将学员转入授权范围外的分中心")

    target_development = updates.get(
        "development_org_unit_id", current["development_org_unit_id"]
    )
    if target_development:
        development = fetch_one(
            "SELECT unit_type, is_active FROM org_units WHERE id=?",
            (target_development,),
        )
        if not development or not development["is_active"]:
            raise ValueError("发展组织不存在或已停用")
        if development["unit_type"] not in {"ROOT", "REGIONAL_CENTER"}:
            raise ValueError("发展组织必须是根节点或分中心")
        if allowed is not None and target_development not in allowed:
            raise PermissionError("不能将学员关联到授权范围外的发展组织")

    relations = fetch_all(
        "SELECT relation_type, org_unit_id FROM member_org_relations "
        "WHERE member_id=? AND (valid_until IS NULL OR valid_until>=?)",
        (member_id, datetime.now(UTC).isoformat()),
    )
    relation_by_type = {row["relation_type"]: row["org_unit_id"] for row in relations}
    class_key_changed = "class_org_unit_id" in updates
    group_key_changed = "group_org_unit_id" in updates
    target_class = (
        updates.get("class_org_unit_id")
        if class_key_changed
        else relation_by_type.get("STUDY_CLASS")
        or relation_by_type.get("SPECIAL_COHORT")
    )
    target_group = (
        updates.get("group_org_unit_id")
        if group_key_changed
        else relation_by_type.get("STUDY_GROUP")
    )
    if class_key_changed and not group_key_changed:
        target_group = None
    class_row = None
    if target_class:
        class_row = fetch_one(
            "SELECT id, name, unit_type, parent_id, is_active FROM org_units WHERE id=?",
            (target_class,),
        )
        if (
            not class_row
            or not class_row["is_active"]
            or class_row["unit_type"] not in {"CLASS", "SPECIAL_COHORT"}
        ):
            raise ValueError("班级组织不存在、已停用或类型不正确")
        if class_row["parent_id"] not in {target_org, "org-suzhou"}:
            raise ValueError("班级不属于所选分中心")
    group_row = None
    if target_group:
        group_row = fetch_one(
            "SELECT id, name, unit_type, parent_id, is_active FROM org_units WHERE id=?",
            (target_group,),
        )
        if (
            not group_row
            or not group_row["is_active"]
            or group_row["unit_type"] != "GROUP"
        ):
            raise ValueError("小组组织不存在、已停用或类型不正确")
        if not class_row:
            raise ValueError("小组必须同时选择所属班级")
        if group_row["parent_id"] != class_row["id"]:
            raise ValueError("小组不属于所选班级")

    phone_fields: dict[str, str | None] = {}
    if "phone" in updates:
        phone = updates["phone"]
        phone_fields = (
            protected_phone(phone)
            if phone and str(phone).strip()
            else {
                "phone_ciphertext": None,
                "phone_hash": None,
                "phone_last4": None,
                "phone_masked": None,
            }
        )
        if phone_fields["phone_hash"]:
            duplicate = fetch_one(
                "SELECT member_code FROM members WHERE phone_hash=? AND id<>? LIMIT 1",
                (phone_fields["phone_hash"], member_id),
            )
            if duplicate:
                raise ValueError(
                    f"手机号已存在其他学员档案（{duplicate['member_code']}），请先人工核对或执行档案合并"
                )

    before = {
        key: current[key]
        for key in (
            "name", "org_unit_id", "development_org_unit_id", "status",
            "phone_masked", "company_name", "notes", "class_name", "group_name",
        )
    }
    now = datetime.now(UTC).isoformat()
    column_values: dict[str, Any] = {}
    for key in ("name", "status", "company_name", "notes"):
        if key in updates:
            column_values[key] = updates[key]
    if "org_unit_id" in updates:
        column_values["org_unit_id"] = target_org
    if "development_org_unit_id" in updates:
        column_values["development_org_unit_id"] = target_development
    if class_key_changed:
        column_values["class_name"] = class_row["name"] if class_row else None
        column_values["group_name"] = group_row["name"] if group_row else None
    elif group_key_changed:
        column_values["group_name"] = group_row["name"] if group_row else None
    column_values.update(phone_fields)
    column_values["updated_at"] = now
    with transaction() as connection:
        assignments = ", ".join(f"{key}=?" for key in column_values)
        execute(
            connection,
            f"UPDATE members SET {assignments} WHERE id=?",
            (*column_values.values(), member_id),
        )

        desired_relations = {
            "PRIMARY_REGION": target_org,
            "DEVELOPMENT_RELATION": target_development,
        }
        if class_key_changed:
            desired_relations["STUDY_CLASS"] = (
                target_class if class_row and class_row["unit_type"] == "CLASS" else None
            )
            desired_relations["SPECIAL_COHORT"] = (
                target_class if class_row and class_row["unit_type"] == "SPECIAL_COHORT" else None
            )
            desired_relations["STUDY_GROUP"] = target_group
        elif group_key_changed:
            desired_relations["STUDY_GROUP"] = target_group
        for relation_type, desired_org in desired_relations.items():
            existing = execute(
                connection,
                "SELECT id FROM member_org_relations WHERE member_id=? AND relation_type=? LIMIT 1",
                (member_id, relation_type),
            ).fetchone()
            if desired_org:
                if existing:
                    execute(
                        connection,
                        "UPDATE member_org_relations SET org_unit_id=?, is_primary=1, "
                        "valid_from=NULL, valid_until=NULL, source_type='MEMBER_UPDATE', updated_at=? WHERE id=?",
                        (desired_org, now, existing["id"]),
                    )
                else:
                    execute(
                        connection,
                        "INSERT INTO member_org_relations(member_id, org_unit_id, relation_type, "
                        "is_primary, source_type, created_at, updated_at) VALUES (?, ?, ?, 1, 'MEMBER_UPDATE', ?, ?)",
                        (member_id, desired_org, relation_type, now, now),
                    )
            elif existing:
                execute(
                    connection,
                    "UPDATE member_org_relations SET is_primary=0, valid_until=?, "
                    "source_type='MEMBER_UPDATE', updated_at=? WHERE id=?",
                    (now, now, existing["id"]),
                )
        after = {
            **before,
            **{key: value for key, value in column_values.items() if key != "updated_at"},
            "phone_masked": phone_fields.get("phone_masked", current["phone_masked"]),
            "class_org_unit_id": target_class,
            "group_org_unit_id": target_group,
        }
        execute(
            connection,
            "INSERT INTO member_change_history(member_id, change_type, before_json, after_json, changed_by, changed_at) "
            "VALUES (?, 'PROFILE_UPDATE', ?, ?, ?, ?)",
            (member_id, json.dumps(before, ensure_ascii=False, default=str),
             json.dumps(after, ensure_ascii=False, default=str), actor_user_id, now),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="members.update",
            resource_type="member",
            resource_id=str(member_id),
            org_unit_id=target_org,
            before=before,
            after=after,
        )
    return member_id


def merge_members(
    actor_user_id: int,
    survivor_member_id: int,
    duplicate_member_id: int,
    reason: str,
) -> int:
    reason = reason.strip()
    if survivor_member_id == duplicate_member_id:
        raise ValueError("主档与重复档案不能是同一条记录")
    if len(reason) < 6:
        raise ValueError("档案合并必须填写至少6个字符的人工确认理由")
    members = fetch_all(
        "SELECT id, member_code, name, org_unit_id, status, phone_masked, company_name, "
        "notes FROM members WHERE id IN (?, ?)",
        (survivor_member_id, duplicate_member_id),
    )
    by_id = {row["id"]: row for row in members}
    survivor = by_id.get(survivor_member_id)
    duplicate = by_id.get(duplicate_member_id)
    if not survivor or not duplicate:
        raise ValueError("主档或重复档案不存在")
    allowed = accessible_org_ids(actor_user_id)
    for member in (survivor, duplicate):
        try:
            resolve_member_scope(member["id"], member["org_unit_id"], allowed)
        except PermissionError as exc:
            raise PermissionError("两个档案都必须在当前组织授权范围内") from exc

    if fetch_one(
        "SELECT 1 FROM member_identities WHERE member_id=?",
        (survivor_member_id,),
    ) and fetch_one(
        "SELECT 1 FROM member_identities WHERE member_id=?",
        (duplicate_member_id,),
    ):
        raise ValueError("两个档案都已绑定身份，不能自动合并，请先处理身份绑定")
    if fetch_one(
        "SELECT 1 FROM renewal_cycles d JOIN renewal_cycles s "
        "ON s.renewal_year=d.renewal_year WHERE d.member_id=? AND s.member_id=? LIMIT 1",
        (duplicate_member_id, survivor_member_id),
    ):
        raise ValueError("两个档案存在相同年度续费周期，不能自动合并")
    if fetch_one(
        "SELECT 1 FROM member_membership_periods d JOIN member_membership_periods s "
        "ON s.membership_year=d.membership_year WHERE d.member_id=? AND s.member_id=? LIMIT 1",
        (duplicate_member_id, survivor_member_id),
    ):
        raise ValueError("两个档案存在相同年度会籍记录，不能自动合并")

    now = datetime.now(UTC).isoformat()
    before = {
        "survivor": {
            key: survivor[key]
            for key in ("id", "member_code", "name", "org_unit_id", "status", "phone_masked")
        },
        "duplicate": {
            key: duplicate[key]
            for key in ("id", "member_code", "name", "org_unit_id", "status", "phone_masked")
        },
    }
    with transaction() as connection:
        duplicate_relations = execute(
            connection,
            "SELECT id, org_unit_id, relation_type FROM member_org_relations WHERE member_id=?",
            (duplicate_member_id,),
        ).fetchall()
        for relation in duplicate_relations:
            existing = execute(
                connection,
                "SELECT id FROM member_org_relations WHERE member_id=? AND org_unit_id=? "
                "AND relation_type=? LIMIT 1",
                (survivor_member_id, relation["org_unit_id"], relation["relation_type"]),
            ).fetchone()
            if existing:
                execute(connection, "DELETE FROM member_org_relations WHERE id=?", (relation["id"],))
            else:
                execute(
                    connection,
                    "UPDATE member_org_relations SET member_id=?, source_type='MEMBER_MERGE', updated_at=? WHERE id=?",
                    (survivor_member_id, now, relation["id"]),
                )

        for table in (
            "attendance_records", "attendance_score_records", "followup_tasks",
            "followup_records", "enterprise_visit_records", "renewal_import_staging",
            "renewal_cycles", "contact_access_logs", "member_change_history",
        ):
            execute(
                connection,
                f"UPDATE {table} SET member_id=? WHERE member_id=?",
                (survivor_member_id, duplicate_member_id),
            )
        execute(
            connection,
            "UPDATE member_identities SET member_id=? WHERE member_id=?",
            (survivor_member_id, duplicate_member_id),
        )
        execute(
            connection,
            "UPDATE member_membership_periods SET member_id=? WHERE member_id=?",
            (survivor_member_id, duplicate_member_id),
        )
        merged_note = f"档案已合并至 {survivor['member_code']}：{reason}"
        execute(
            connection,
            "UPDATE members SET status='INACTIVE', notes=?, updated_at=? WHERE id=?",
            (merged_note, now, duplicate_member_id),
        )
        after = {
            "survivor_member_id": survivor_member_id,
            "duplicate_member_id": duplicate_member_id,
            "migrated_relations": len(duplicate_relations),
            "status": "INACTIVE",
        }
        execute(
            connection,
            "INSERT INTO member_merge_history(survivor_member_id, duplicate_member_id, reason, "
            "before_json, after_json, merged_by, merged_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                survivor_member_id, duplicate_member_id, reason,
                json.dumps(before, ensure_ascii=False, default=str),
                json.dumps(after, ensure_ascii=False, default=str), actor_user_id, now,
            ),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="members.merge",
            resource_type="member",
            resource_id=str(survivor_member_id),
            org_unit_id=survivor["org_unit_id"],
            purpose=reason,
            before=before,
            after=after,
        )
    return survivor_member_id


def list_members(
    user_id: int,
    org_unit_id: str | None = None,
    *,
    include_company_name: bool = False,
) -> list[dict[str, Any]]:
    """Return the minimum member summary needed for list views.

    Company name is retained only for the explicitly authorized normal export
    path; it is never part of the ordinary list response.
    """
    params: list[Any] = []
    company_column = ", m.company_name" if include_company_name else ""
    sql = (
        "SELECT m.id, m.member_code, m.name, m.org_unit_id, o.name AS org_name, "
        "m.status, m.phone_masked, m.phone_last4, m.class_name, m.group_name"
        f"{company_column} "
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
    def record_attempt(result: str, task: dict[str, Any] | None = None) -> None:
        with transaction() as connection:
            write_audit(
                connection,
                actor_user_id=actor_user_id,
                action="members.contact.reveal",
                resource_type="member",
                resource_id=str(member_id),
                org_unit_id=task["org_unit_id"] if task else None,
                purpose=purpose,
                result=result,
                after={
                    "task_id": task_id,
                    "client_reference": client_reference,
                },
            )
            if task and fetch_one("SELECT id FROM members WHERE id=?", (member_id,)):
                execute(
                    connection,
                    "INSERT INTO contact_access_logs(task_id, member_id, actor_user_id, purpose, "
                    "result, client_reference, accessed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        task_id, member_id, actor_user_id, purpose, result,
                        client_reference, datetime.now(UTC).isoformat(),
                    ),
                )

    purpose = purpose.strip()
    if len(purpose) < 4:
        record_attempt("DENIED_INVALID_PURPOSE")
        raise ValueError("必须填写本次联系用途")
    task = fetch_one(
        "SELECT id, member_id, org_unit_id, assigned_user_id, status, due_at FROM followup_tasks WHERE id=?",
        (task_id,),
    )
    if not task or task["member_id"] != member_id:
        record_attempt("DENIED_TASK_MISMATCH", task)
        raise PermissionError("联系任务与学长不匹配")
    now = datetime.now(UTC)
    if task["assigned_user_id"] != actor_user_id or task["status"] not in {"OPEN", "IN_PROGRESS"}:
        record_attempt("DENIED_TASK_OWNER_OR_STATUS", task)
        raise PermissionError("只有当前有效任务责任人可以查看")
    from app.services.followup_invitations import is_primary_assignee

    if not is_primary_assignee(task_id, actor_user_id):
        record_attempt("DENIED_INVITATION", task)
        raise PermissionError("接受服务邀请后才可以查看本次联系信息")
    if task["due_at"] and _as_utc(task["due_at"]) < now:
        record_attempt("DENIED_EXPIRED", task)
        raise PermissionError("联系任务已过期")
    allowed = accessible_org_ids(actor_user_id)
    if allowed is not None and task["org_unit_id"] not in allowed:
        record_attempt("DENIED_ORG_SCOPE", task)
        raise PermissionError("任务不在组织授权范围内")
    member = fetch_one(
        "SELECT id, name, phone_ciphertext, phone_masked FROM members WHERE id=?", (member_id,)
    )
    if not member or not member["phone_ciphertext"]:
        record_attempt("DENIED_NO_PHONE", task)
        raise ValueError("学长没有可用联系方式")
    try:
        phone = decrypt_text(member["phone_ciphertext"])
    except ValueError:
        record_attempt("DECRYPTION_FAILED", task)
        raise
    record_attempt("SUCCESS", task)
    return {"name": member["name"], "phone": phone, "expires_in": "60秒"}


def get_member_detail(member_id: int, actor_user_id: int) -> dict[str, Any]:
    """返回学长基本资料，手机号脱敏，不含企业敏感财务数据。

    完整手机号须经过 reveal_contact（任务用途控制）。
    完整企业敏感资料须经过 get_member_enterprise_detail（用途+审计）。
    """
    user = user_context(actor_user_id)
    if not user or "members:detail_view" not in user["permissions"]:
        raise PermissionError("当前角色不能查看学长资料")
    member = fetch_one(
        "SELECT m.id, m.name, m.org_unit_id, o.name AS org_name, "
        "m.phone_masked, m.phone_last4, "
        "m.gender, m.birthday, m.district, m.class_name, m.group_name, m.join_date, "
        "m.study_start_date, m.membership_years, m.renewal_month, m.status, m.position, "
        "m.referrer, m.referrer_center "
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


def get_member_timeline(
    member_id: int, actor_user_id: int, *, limit: int = 100
) -> dict[str, Any]:
    """Return a privacy-safe, read-only service timeline for one member.

    Timeline entries intentionally contain event metadata only. Follow-up notes,
    renewal summaries and enterprise visit narratives remain behind their own
    task/cycle permissions and are never copied into this aggregate response.
    """
    user = user_context(actor_user_id)
    if not user or "members:detail_view" not in user["permissions"]:
        raise PermissionError("当前角色不能查看学长服务时间线")
    member = fetch_one(
        "SELECT m.id, m.name, m.org_unit_id, o.name AS org_name, m.phone_masked, "
        "m.class_name, m.group_name, m.status "
        "FROM members m JOIN org_units o ON o.id=m.org_unit_id WHERE m.id=?",
        (member_id,),
    )
    if not member:
        raise ValueError("学长不存在")
    allowed = accessible_org_ids(actor_user_id)
    if not can_access_member(member_id, member["org_unit_id"], allowed):
        raise PermissionError("学长不在组织授权范围内")

    def timestamp(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.astimezone(UTC).isoformat()
        return str(value)

    events: list[dict[str, Any]] = []
    changes = fetch_all(
        "SELECT id, change_type, changed_by, changed_at FROM member_change_history "
        "WHERE member_id=? ORDER BY changed_at DESC, id DESC",
        (member_id,),
    )
    for row in changes:
        events.append(
            {
                "id": f"member-change-{row['id']}",
                "event_type": "PROFILE_CHANGE",
                "occurred_at": timestamp(row["changed_at"]),
                "title": "学员档案变更",
                "status": row["change_type"],
                "actor_id": row["changed_by"],
            }
        )

    if "members:read" in user["permissions"]:
        attendance_rows = fetch_all(
            "SELECT ar.id, ar.attendance_status, ar.checked_at, ar.participant_type, "
            "s.session_code, s.session_name, g.event_date, g.title, g.activity_type "
            "FROM attendance_records ar "
            "JOIN attendance_sessions s ON s.id=ar.attendance_session_id "
            "JOIN attendance_event_groups g ON g.id=s.event_group_id "
            "WHERE ar.member_id=? ORDER BY COALESCE(ar.checked_at, g.event_date) DESC, ar.id DESC",
            (member_id,),
        )
        for row in attendance_rows:
            events.append(
                {
                    "id": f"attendance-{row['id']}",
                    "event_type": "ATTENDANCE",
                    "occurred_at": timestamp(row["checked_at"] or row["event_date"]),
                    "title": row["title"] or "签到活动",
                    "status": row["attendance_status"],
                    "channel": row["session_name"] or row["session_code"],
                    "activity_type": row["activity_type"],
                    "participant_type": row["participant_type"],
                }
            )

    if "followups:manage" in user["permissions"]:
        task_rows = fetch_all(
            "SELECT id, org_unit_id, task_type, status, confidentiality_level, "
            "assigned_user_id, created_at, due_at, updated_at "
            "FROM followup_tasks WHERE member_id=? ORDER BY created_at DESC, id DESC",
            (member_id,),
        )
        from app.services.followup_invitations import can_participate

        visible_task_ids: list[int] = []
        for row in task_rows:
            if row["confidentiality_level"] == "ASSIGNEE" and not can_participate(
                row["id"], actor_user_id
            ):
                continue
            visible_task_ids.append(row["id"])
            events.append(
                {
                    "id": f"followup-task-{row['id']}",
                    "event_type": "FOLLOWUP_TASK",
                    "occurred_at": timestamp(row["created_at"]),
                    "title": "关怀服务事项",
                    "status": row["status"],
                    "channel": row["task_type"],
                    "due_at": timestamp(row["due_at"]),
                    "updated_at": timestamp(row["updated_at"]),
                }
            )
        if visible_task_ids:
            placeholders = ",".join("?" for _ in visible_task_ids)
            record_rows = fetch_all(
                "SELECT id, channel, contacted_at, outcome_code FROM followup_records "
                f"WHERE member_id=? AND task_id IN ({placeholders}) "
                "ORDER BY contacted_at DESC, id DESC",
                (member_id, *visible_task_ids),
            )
            for row in record_rows:
                events.append(
                    {
                        "id": f"followup-record-{row['id']}",
                        "event_type": "FOLLOWUP_RECORD",
                        "occurred_at": timestamp(row["contacted_at"]),
                        "title": "关怀服务记录",
                        "status": row["outcome_code"],
                        "channel": row["channel"],
                    }
                )
            visit_rows = fetch_all(
                "SELECT id, visited_at, location_type FROM enterprise_visit_records "
                f"WHERE member_id=? AND task_id IN ({placeholders}) "
                "ORDER BY visited_at DESC, id DESC",
                (member_id, *visible_task_ids),
            )
            for row in visit_rows:
                events.append(
                    {
                        "id": f"enterprise-visit-{row['id']}",
                        "event_type": "ENTERPRISE_VISIT",
                        "occurred_at": timestamp(row["visited_at"]),
                        "title": "企业走访",
                        "status": "已记录",
                        "channel": row["location_type"],
                    }
                )

    if "renewals:read" in user["permissions"]:
        cycle_rows = fetch_all(
            "SELECT c.id, c.renewal_year, c.org_unit_id, c.due_month, c.phase, c.status, "
            "c.completed_at, c.updated_at FROM renewal_cycles c WHERE c.member_id=? "
            "ORDER BY c.renewal_year DESC, c.id DESC",
            (member_id,),
        )
        visible_cycle_ids: list[int] = []
        for row in cycle_rows:
            if allowed is not None and row["org_unit_id"] not in allowed:
                continue
            visible_cycle_ids.append(row["id"])
            events.append(
                {
                    "id": f"renewal-cycle-{row['id']}",
                    "event_type": "RENEWAL_CYCLE",
                    "occurred_at": timestamp(row["updated_at"]),
                    "title": "续费周期",
                    "status": row["status"],
                    "channel": f"{row['renewal_year']}年{row['due_month']}月",
                    "phase": row["phase"],
                }
            )
        if visible_cycle_ids:
            placeholders = ",".join("?" for _ in visible_cycle_ids)
            renewal_rows = fetch_all(
                "SELECT id, followed_at, channel FROM renewal_followups "
                f"WHERE renewal_cycle_id IN ({placeholders}) ORDER BY followed_at DESC, id DESC",
                tuple(visible_cycle_ids),
            )
            for row in renewal_rows:
                events.append(
                    {
                        "id": f"renewal-followup-{row['id']}",
                        "event_type": "RENEWAL_FOLLOWUP",
                        "occurred_at": timestamp(row["followed_at"]),
                        "title": "续费跟进",
                        "status": "已记录",
                        "channel": row["channel"],
                    }
                )

    events.sort(key=lambda item: (item.get("occurred_at") or "", item["id"]), reverse=True)
    visible_events = events[: max(1, min(limit, 200))]
    summary: dict[str, int] = {}
    for event in events:
        summary[event["event_type"]] = summary.get(event["event_type"], 0) + 1
    with transaction() as connection:
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="members.timeline.view",
            resource_type="member",
            resource_id=str(member_id),
            org_unit_id=member["org_unit_id"],
            after={"event_count": len(visible_events)},
        )
    return {
        "member": dict(member),
        "summary": summary,
        "events": visible_events,
    }


def get_member_enterprise_detail(
    member_id: int, actor_user_id: int, purpose: str
) -> dict[str, Any]:
    """返回企业敏感资料，不返回完整手机号，必须填写用途并写审计。

    仅具有 members:enterprise_view 用途权限的岗位可调用。
    """
    purpose = purpose.strip()
    if len(purpose) < 4:
        raise ValueError("必须填写查看用途（至少4个字符）")
    user = user_context(actor_user_id)
    if not user or "members:enterprise_view" not in user["permissions"]:
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
    rows = list_members(user_id, include_company_name=True)
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
    if not user or "exports:sensitive" not in user["permissions"]:
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
    if not user or "exports:sensitive" not in user["permissions"]:
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
