from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.db import execute, fetch_one, transaction
from app.services.birthday_greetings import (
    generate_birthday_greeting_draft,
    get_birthday_greeting_context,
)
from app.services.iam import create_user


def _insert_scope() -> tuple[str, str, int]:
    suffix = uuid4().hex[:8]
    center_id = f"birthday-center-{suffix}"
    class_id = f"birthday-class-{suffix}"
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES (?, ?, '生日测试分中心', 'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
            (center_id, f"BIRTHDAY_CENTER_{suffix}", now, now),
        )
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES (?, ?, '生日测试班', 'CLASS', ?, 1, ?, ?)",
            (class_id, f"BIRTHDAY_CLASS_{suffix}", center_id, now, now),
        )
        member_id = execute(
            connection,
            "INSERT INTO members(member_code, name, org_unit_id, status, birthday, join_date, created_at, updated_at) "
            "VALUES (?, '生日测试学长', ?, 'ACTIVE', '1980-08-26', '2021-03-18', ?, ?)",
            (f"BIRTHDAY_MEMBER_{suffix}", center_id, now, now),
        ).lastrowid
        for relation_type, org_id in (("PRIMARY_REGION", center_id), ("STUDY_CLASS", class_id)):
            execute(
                connection,
                "INSERT INTO member_org_relations(member_id, org_unit_id, relation_type, is_primary, source_type, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, 'TEST', ?, ?)",
                (member_id, org_id, relation_type, now, now),
            )
        event_id = execute(
            connection,
            "INSERT INTO attendance_event_groups(source_key, external_group_id, org_unit_id, study_org_unit_id, title, activity_type, event_date, status, created_at, updated_at) "
            "VALUES ('birthday-test', ?, ?, ?, '经营十二条专题课程', 'COURSE', '2022-06-12', 'ACTIVE', ?, ?)",
            (f"EVENT_{suffix}", center_id, class_id, now, now),
        ).lastrowid
        for session_code in ("MORNING", "AFTERNOON"):
            session_id = execute(
                connection,
                "INSERT INTO attendance_sessions(event_group_id, external_session_id, session_code, session_name, session_order, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 1, 'ACTIVE', ?, ?)",
                (event_id, f"{suffix}-{session_code}", session_code, session_code, now, now),
            ).lastrowid
            execute(
                connection,
                "INSERT INTO attendance_records(attendance_session_id, member_id, participant_type, score_eligible, attendance_status, checked_at, received_at, created_at, updated_at) "
                "VALUES (?, ?, 'MEMBER', 1, 'PRESENT', '2022-06-12T08:00:00', ?, ?, ?)",
                (session_id, member_id, now, now, now),
            )
        absent_group = execute(
            connection,
            "INSERT INTO attendance_event_groups(source_key, external_group_id, org_unit_id, title, activity_type, event_date, status, created_at, updated_at) "
            "VALUES ('birthday-test', ?, ?, '未参加课程', 'COURSE', '2023-06-12', 'ACTIVE', ?, ?)",
            (f"ABSENT_{suffix}", center_id, now, now),
        ).lastrowid
        absent_session = execute(
            connection,
            "INSERT INTO attendance_sessions(event_group_id, external_session_id, session_code, session_name, session_order, status, created_at, updated_at) "
            "VALUES (?, ?, 'MORNING', '上午场', 1, 'ACTIVE', ?, ?)",
            (absent_group, f"absent-{suffix}", now, now),
        ).lastrowid
        execute(
            connection,
            "INSERT INTO attendance_records(attendance_session_id, member_id, participant_type, score_eligible, attendance_status, received_at, created_at, updated_at) "
            "VALUES (?, ?, 'MEMBER', 1, 'ABSENT', ?, ?, ?)",
            (absent_session, member_id, now, now, now),
        )
    return center_id, class_id, member_id


def test_context_contains_only_verified_deduplicated_memories() -> None:
    center_id, class_id, member_id = _insert_scope()
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    user_id = create_user(
        admin["id"],
        username=f"birthday-user-{uuid4().hex[:8]}",
        display_name="生日关怀测试",
        password="birthday-test-password",
        roles=["ops_center_operations"],
        scopes=[{"scope_type": "SUBTREE", "org_unit_id": center_id}],
    )
    context = get_birthday_greeting_context(member_id, user_id)
    assert context["member"]["join_date"] == "2021-03-18"
    assert context["member"]["membership_years"] is not None
    assert context["member"]["class_org_unit_id"] == class_id
    assert len(context["memories"]) == 1
    assert context["memories"][0]["title"] == "经营十二条专题课程"
    assert context["memories"][0]["verified"] is True
    assert context["data_quality"]["facts_only"] is True


def test_draft_rejects_unverified_memory_ids_and_uses_selected_facts() -> None:
    center_id, _, member_id = _insert_scope()
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    user_id = create_user(
        admin["id"],
        username=f"birthday-draft-{uuid4().hex[:8]}",
        display_name="生日草稿测试",
        password="birthday-test-password",
        roles=["ops_center_operations"],
        scopes=[{"scope_type": "SUBTREE", "org_unit_id": center_id}],
    )
    with pytest.raises(ValueError):
        generate_birthday_greeting_draft(
            member_id, user_id, selected_memory_ids=["made-up-fact"], tone="warm"
        )
    context = get_birthday_greeting_context(member_id, user_id)
    result = generate_birthday_greeting_draft(
        member_id,
        user_id,
        selected_memory_ids=context["selected_memory_ids"],
        tone="concise",
    )
    assert result["facts_only"] is True
    assert result["draft"].splitlines()[0] == "生日测试学长好，8月26日是您的生日，祝您生日快乐！"
    assert "新的一岁" not in result["draft"]
    assert "2022年6月的经营十二条专题课程" in result["draft"]
    assert "企业收入" not in result["draft"]

    warm_result = generate_birthday_greeting_draft(
        member_id,
        user_id,
        selected_memory_ids=context["selected_memory_ids"],
        tone="warm",
    )
    warm_lines = warm_result["draft"].splitlines()
    assert warm_lines[0] == "生日测试学长好，8月26日是您的生日，祝您生日快乐！"
    assert warm_lines[1] == "从2021年3月加入盛和塾以来，已经与盛和塾各位学长同行5年。"
    assert warm_lines[2] == "这些年的学习与相聚，记录着与各位学长共同走过的时光。"
    assert "感谢您这些年与盛和塾各位学长同行与践行" in warm_lines[3]
    assert "2022年6月的经营十二条专题课程" not in warm_result["draft"]
