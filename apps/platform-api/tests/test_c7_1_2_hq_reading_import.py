from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from uuid import uuid4

import pytest
from openpyxl import Workbook

from app.db import execute, fetch_all, fetch_one, transaction
from app.main import read_only_request_allowed
from app.services.hq_reading_import import (
    HQ_SOURCE_TYPE,
    confirm_hq_reading_identities,
    dry_run_hq_reading_import,
    import_hq_reading_workbook,
    parse_hq_reading_workbook,
    record_manual_verified_excellent_share,
)
from app.services.learning_activity_credits import (
    DAILY_READING,
    dry_run_excellent_shares,
)
from app.services.learning_credits import LearningCreditError


@pytest.fixture(autouse=True)
def c712_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _admin_id() -> int:
    return int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])


def _policy_ids() -> tuple[int, int]:
    row = fetch_one(
        "SELECT generic_rule_version_id, course_credit_rule_version_id "
        "FROM learning_plan_credit_rule_mappings "
        "WHERE plan_key='standard-3y' AND plan_version_label='2026' "
        "AND status='ACTIVE' LIMIT 1"
    )
    assert row
    return int(row["generic_rule_version_id"]), int(row["course_credit_rule_version_id"])


def _hq_fixture(
    specs: list[dict] | None = None,
    *,
    group_names: tuple[str, ...] = ("1组", "2组", "精进组"),
) -> dict:
    suffix = uuid4().hex[:12]
    center_id = f"hq-center-{suffix}"
    class_id = f"hq-class-{suffix}"
    now = _now()
    generic_rule_id, course_rule_id = _policy_ids()
    specs = specs or [
        {
            "key": "learner",
            "name": "总部导入测试学员",
            "group": "1组",
            "masked_account": "138****1001",
            "last4": "1001",
        }
    ]
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES (?, ?, 'C7.1.2测试分中心', 'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
            (center_id, f"HQ_CENTER_{suffix}", now, now),
        )
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES (?, ?, 'C7.1.2测试班', 'CLASS', ?, 1, ?, ?)",
            (class_id, f"HQ_CLASS_{suffix}", center_id, now, now),
        )
        group_ids: dict[str, str] = {}
        for index, group_name in enumerate(group_names, start=1):
            group_id = f"hq-group-{suffix}-{index}"
            group_ids[group_name] = group_id
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, 'GROUP', ?, 1, ?, ?)",
                (group_id, f"HQ_GROUP_{suffix}_{index}", group_name, class_id, now, now),
            )
        member_ids: dict[str, int] = {}
        for index, spec in enumerate(specs, start=1):
            key = str(spec["key"])
            member_code = f"HQM-{suffix}-{index}"
            member_cursor = execute(
                connection,
                "INSERT INTO members(member_code, name, org_unit_id, status, phone_last4, phone_masked, created_at, updated_at) "
                "VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?, ?)",
                (
                    member_code,
                    spec["name"],
                    class_id,
                    spec.get("last4"),
                    spec.get("masked_account"),
                    now,
                    now,
                ),
            )
            member_id = int(member_cursor.lastrowid)
            member_ids[key] = member_id
            execute(
                connection,
                "INSERT INTO member_org_relations "
                "(member_id, org_unit_id, relation_type, is_primary, valid_from, valid_until, created_at, updated_at) "
                "VALUES (?, ?, 'STUDY_CLASS', 1, '2020-01-01', NULL, ?, ?)",
                (member_id, class_id, now, now),
            )
            if spec.get("group"):
                execute(
                    connection,
                    "INSERT INTO member_org_relations "
                    "(member_id, org_unit_id, relation_type, is_primary, valid_from, valid_until, created_at, updated_at) "
                    "VALUES (?, ?, 'STUDY_GROUP', 1, '2020-01-01', NULL, ?, ?)",
                    (member_id, group_ids[spec["group"]], now, now),
                )
        plan_cursor = execute(
            connection,
            "INSERT INTO learning_plan_versions "
            "(plan_key, plan_name, version_label, duration_cycles, status, created_at, updated_at) "
            "VALUES (?, 'C7.1.2测试计划', '2026', 36, 'PUBLISHED', ?, ?)",
            (f"HQ_{suffix}", now, now),
        )
        binding_cursor = execute(
            connection,
            "INSERT INTO class_learning_bindings "
            "(class_org_unit_id, plan_version_id, cohort_month, started_at, status, "
            "learning_round, start_cycle_index, transition_type, credit_rule_version_id, "
            "course_credit_rule_version_id, created_at, updated_at) "
            "VALUES (?, ?, 1, '2026-01-01T00:00:00+00:00', 'ACTIVE', 1, 1, 'INITIAL', ?, ?, ?, ?)",
            (class_id, int(plan_cursor.lastrowid), generic_rule_id, course_rule_id, now, now),
        )
    return {
        "suffix": suffix,
        "center_id": center_id,
        "class_id": class_id,
        "group_ids": group_ids,
        "member_ids": member_ids,
        "binding_id": int(binding_cursor.lastrowid),
    }


def _calendar(fixture: dict, dates: dict[str, str]) -> int:
    now = _now()
    with transaction() as connection:
        cursor = execute(
            connection,
            "INSERT INTO learning_business_calendar_versions "
            "(calendar_key, calendar_year, version_label, timezone, status, created_at, updated_at) "
            "VALUES ('CHINA_MAINLAND', 2026, ?, 'Asia/Shanghai', 'PUBLISHED', ?, ?)",
            (f"C7.1.2-{fixture['suffix']}", now, now),
        )
        version_id = int(cursor.lastrowid)
        for business_date, day_type in dates.items():
            execute(
                connection,
                "INSERT INTO learning_business_calendar_days "
                "(calendar_version_id, business_date, day_type, note, created_at, updated_at) "
                "VALUES (?, ?, ?, 'C7.1.2 isolated test', ?, ?)",
                (version_id, business_date, day_type, now, now),
            )
    return version_id


def _remove_calendar(version_id: int) -> None:
    with transaction() as connection:
        execute(
            connection,
            "DELETE FROM learning_business_calendar_versions WHERE id=?",
            (version_id,),
        )


def _xlsx(rows: list[dict], *, extra_headers: list[str] | None = None) -> bytes:
    headers = ["日期", "姓名", "小组", "录音", "账号", "工作人员", "学习资格"]
    headers.extend(extra_headers or [])
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "每日明细"
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(header) for header in headers])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _row(
    *,
    name: str,
    group: str | None = "1组",
    occurred_on: str = "2026-03-02",
    recording: str | None = "按时完成",
    account: str | None = None,
    staff: str | None = None,
    qualification: str | None = None,
    **extra: str,
) -> dict:
    result = {
        "日期": occurred_on,
        "姓名": name,
        "小组": group,
        "录音": recording,
        "账号": account,
        "工作人员": staff,
        "学习资格": qualification,
    }
    result.update(extra)
    return result


def _import(fixture: dict, rows: list[dict], *, extra_headers: list[str] | None = None) -> dict:
    return import_hq_reading_workbook(
        actor_user_id=_admin_id(),
        target_class_org_unit_id=str(fixture["class_id"]),
        original_filename=f"hq-{fixture['suffix']}.xlsx",
        content=_xlsx(rows, extra_headers=extra_headers),
    )


def _batch_id(result: dict) -> int:
    return int(result.get("batch", {}).get("batch_id") or result["summary"]["batch_id"])


def _observations(batch_id: int) -> list[dict]:
    return fetch_all(
        "SELECT * FROM hq_reading_import_observations WHERE batch_id=? ORDER BY id",
        (batch_id,),
    )


def _confirm(
    result: dict, mapping: dict[tuple, int], *, names: set[str] | None = None
) -> dict:
    confirmations = []
    for identity in result["identities"]:
        if names is not None and identity["source_name"] not in names:
            continue
        key = (
            identity["source_name"],
            identity.get("source_group_name"),
            identity.get("source_masked_account"),
        )
        confirmations.append(
            {
                "source_identity_key": identity["source_identity_key"],
                "member_id": mapping[key],
            }
        )
    return confirm_hq_reading_identities(
        actor_user_id=_admin_id(),
        batch_id=_batch_id(result),
        confirmations=confirmations,
    )


def _hq_fact_count(fixture: dict | None = None) -> int:
    clause = "source_type=?"
    params: tuple = (HQ_SOURCE_TYPE,)
    if fixture is not None:
        clause += " AND class_org_unit_id=?"
        params += (fixture["class_id"],)
    return int(
        fetch_one(
            f"SELECT COUNT(*) AS n FROM learning_credit_activity_facts WHERE {clause}",
            params,
        )["n"]
    )


def _ledger_count() -> int:
    return int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"])


def test_import_is_scoped_to_selected_class_and_never_guesses_cross_class_member() -> None:
    fixture = _hq_fixture(specs=[])
    suffix = fixture["suffix"]
    other_class = f"hq-other-class-{suffix}"
    other_group = f"hq-other-group-{suffix}"
    now = _now()
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES (?, ?, '其他测试班', 'CLASS', ?, 1, ?, ?)",
            (other_class, f"HQ_OTHER_CLASS_{suffix}", fixture["center_id"], now, now),
        )
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES (?, ?, '1组', 'GROUP', ?, 1, ?, ?)",
            (other_group, f"HQ_OTHER_GROUP_{suffix}", other_class, now, now),
        )
        member_cursor = execute(
            connection,
            "INSERT INTO members(member_code, name, org_unit_id, status, created_at, updated_at) "
            "VALUES (?, '跨班同名学员', ?, 'ACTIVE', ?, ?)",
            (f"HQ_OTHER_MEMBER_{suffix}", other_class, now, now),
        )
        execute(
            connection,
            "INSERT INTO member_org_relations "
            "(member_id, org_unit_id, relation_type, is_primary, valid_from, created_at, updated_at) "
            "VALUES (?, ?, 'STUDY_CLASS', 1, '2020-01-01', ?, ?)",
            (int(member_cursor.lastrowid), other_class, now, now),
        )
    result = _import(fixture, [_row(name="跨班同名学员")])
    assert result["identities"][0]["match_status"] == "MEMBER_MAPPING_REQUIRED"
    assert _hq_fact_count(fixture) == 0


def test_group_types_drive_personal_and_class_rate_flags() -> None:
    fixture = _hq_fixture(
        specs=[
            {"key": "regular", "name": "普通完成", "group": "1组"},
            {"key": "advanced", "name": "精进完成", "group": "精进组"},
            {"key": "nogroup", "name": "空小组", "group": None},
            {"key": "incomplete", "name": "普通未完成", "group": "2组"},
        ]
    )
    rows = [
        _row(name="普通完成"),
        _row(name="精进完成", group="精进组"),
        _row(name="空小组", group=None),
        _row(name="普通未完成", group="2组", recording="未提交"),
    ]
    imported = _import(fixture, rows)
    mapping = {
        ("普通完成", "1组", None): fixture["member_ids"]["regular"],
        ("精进完成", "精进组", None): fixture["member_ids"]["advanced"],
        ("空小组", None, None): fixture["member_ids"]["nogroup"],
        ("普通未完成", "2组", None): fixture["member_ids"]["incomplete"],
    }
    _confirm(imported, mapping)
    observations = {row["source_name"]: row for row in _observations(_batch_id(imported))}
    assert observations["普通完成"]["group_type"] == "CLASS_REGULAR"
    assert observations["普通完成"]["personal_credit_eligible"] == 1
    assert observations["普通完成"]["class_rate_denominator_eligible"] == 1
    assert observations["普通完成"]["class_rate_numerator_eligible"] == 1
    assert observations["精进完成"]["group_type"] == "CLASS_ADVANCED"
    assert observations["精进完成"]["personal_credit_eligible"] == 1
    assert observations["精进完成"]["class_rate_denominator_eligible"] == 0
    assert observations["空小组"]["group_type"] == "NO_GROUP"
    assert observations["空小组"]["personal_credit_eligible"] == 0
    assert observations["空小组"]["eligibility_reason"] == "NO_GROUP"
    assert observations["普通未完成"]["class_rate_denominator_eligible"] == 1
    assert observations["普通未完成"]["class_rate_numerator_eligible"] == 0
    calendar_id = _calendar(fixture, {"2026-03-02": "NORMAL_WORKDAY"})
    try:
        preview = dry_run_hq_reading_import(
            actor_user_id=_admin_id(), batch_id=_batch_id(imported)
        )
    finally:
        _remove_calendar(calendar_id)
    details = {row["source_name"]: row for row in preview["details"]}
    assert details["普通完成"]["projected_status"] == "READY"
    assert details["普通完成"]["projected_points"] == 1.0
    assert details["精进完成"]["projected_status"] == "READY"
    assert details["空小组"]["projected_status"] == "BLOCKED"
    assert details["普通未完成"]["projected_status"] == "BLOCKED"
    assert preview["class_rates"] == [
        {
            "occurred_on": "2026-03-02",
            "denominator_person_count": 2,
            "numerator_person_count": 1,
            "rate": 0.5,
            "denominator_basis": "unique_person_id",
            "numerator_basis": "unique_person_id",
        }
    ]


def test_matching_uses_group_and_masked_account_and_never_name_only() -> None:
    fixture = _hq_fixture(
        specs=[
            {"key": "same_a", "name": "重名学员", "group": "1组", "masked_account": "138****2001", "last4": "2001"},
            {"key": "same_b", "name": "重名学员", "group": "1组", "masked_account": "138****2002", "last4": "2002"},
            {"key": "group_two", "name": "重名学员", "group": "2组"},
        ]
    )
    result = _import(
        fixture,
        [
            _row(name="重名学员", group="1组", account="138****2002"),
            _row(name="重名学员", group="1组", occurred_on="2026-03-03"),
            _row(name="重名学员", group="2组"),
        ],
    )
    identities = {(item["source_group_name"], item.get("source_masked_account")): item for item in result["identities"]}
    assert identities[("1组", "138****2002")]["match_status"] == "AUTO_MATCHED"
    assert identities[("1组", "138****2002")]["member_id"] == fixture["member_ids"]["same_b"]
    assert identities[("1组", None)]["match_status"] == "MEMBER_MAPPING_REQUIRED"
    assert identities[("2组", None)]["match_status"] == "CANDIDATE"


def test_confirmed_source_identity_reuses_binding_and_incomplete_fact_progresses() -> None:
    fixture = _hq_fixture(specs=[{"key": "learner", "name": "后续完成", "group": "1组"}])
    first = _import(fixture, [_row(name="后续完成", recording="未提交")])
    _confirm(first, {("后续完成", "1组", None): fixture["member_ids"]["learner"]})
    assert _hq_fact_count(fixture) == 0
    second = _import(
        fixture,
        [_row(name="后续完成", recording="按时完成", 备注="总部修订后的事实")],
        extra_headers=["备注"],
    )
    observation = _observations(_batch_id(second))[0]
    assert observation["match_status"] == "CONFIRMED_BINDING"
    assert observation["recording_status"] == "COMPLETE"
    assert _hq_fact_count(fixture) == 1
    fact = fetch_one(
        "SELECT * FROM learning_credit_activity_facts WHERE source_type=?",
        (HQ_SOURCE_TYPE,),
    )
    assert fact and fact["occurred_on"] == "2026-03-02"


def test_reimport_keeps_source_fact_on_original_learning_round_after_new_round_starts() -> None:
    fixture = _hq_fixture(specs=[{"key": "learner", "name": "冻结轮次", "group": "1组"}])
    first = _import(fixture, [_row(name="冻结轮次")])
    _confirm(first, {("冻结轮次", "1组", None): fixture["member_ids"]["learner"]})
    old_binding_id = int(fixture["binding_id"])
    fact_before = fetch_one(
        "SELECT binding_id FROM learning_credit_activity_facts "
        "WHERE source_type=? AND class_org_unit_id=?",
        (HQ_SOURCE_TYPE, fixture["class_id"]),
    )
    assert fact_before and int(fact_before["binding_id"]) == old_binding_id
    now = _now()
    generic_rule_id, course_rule_id = _policy_ids()
    with transaction() as connection:
        execute(
            connection,
            "UPDATE class_learning_bindings SET status='ENDED', ended_at=?, ended_reason='C7.1.2测试换轮次', updated_at=? WHERE id=?",
            ("2026-03-31T23:59:59+00:00", now, old_binding_id),
        )
        plan_cursor = execute(
            connection,
            "INSERT INTO learning_plan_versions "
            "(plan_key, plan_name, version_label, duration_cycles, status, created_at, updated_at) "
            "VALUES (?, 'C7.1.2后续计划', '2026', 36, 'PUBLISHED', ?, ?)",
            (f"HQ_NEXT_{fixture['suffix']}", now, now),
        )
        execute(
            connection,
            "INSERT INTO class_learning_bindings "
            "(class_org_unit_id, plan_version_id, cohort_month, started_at, status, "
            "learning_round, start_cycle_index, transition_type, credit_rule_version_id, "
            "course_credit_rule_version_id, created_at, updated_at) "
            "VALUES (?, ?, 4, '2026-04-01T00:00:00+00:00', 'ACTIVE', 2, 1, 'RESTART', ?, ?, ?, ?)",
            (fixture["class_id"], int(plan_cursor.lastrowid), generic_rule_id, course_rule_id, now, now),
        )
    second = _import(fixture, [_row(name="冻结轮次")], extra_headers=["备注"])
    assert second["duplicate"] is False
    fact_after = fetch_one(
        "SELECT binding_id FROM learning_credit_activity_facts "
        "WHERE source_type=? AND class_org_unit_id=?",
        (HQ_SOURCE_TYPE, fixture["class_id"]),
    )
    assert fact_after and int(fact_after["binding_id"]) == old_binding_id


def test_platform_group_move_is_group_mismatch_and_does_not_mutate_org_relation() -> None:
    fixture = _hq_fixture(
        specs=[
            {
                "key": "moved",
                "name": "平台换组",
                "group": "1组",
                "masked_account": "138****3001",
                "last4": "3001",
            }
        ]
    )
    row = _row(name="平台换组", account="138****3001")
    first = _import(fixture, [row])
    _confirm(first, {("平台换组", "1组", "138****3001"): fixture["member_ids"]["moved"]})
    before = fetch_all(
        "SELECT org_unit_id, relation_type FROM member_org_relations "
        "WHERE member_id=? ORDER BY relation_type, org_unit_id",
        (fixture["member_ids"]["moved"],),
    )
    with transaction() as connection:
        execute(
            connection,
            "DELETE FROM member_org_relations WHERE member_id=? AND org_unit_id=? AND relation_type='STUDY_GROUP'",
            (fixture["member_ids"]["moved"], fixture["group_ids"]["1组"]),
        )
        execute(
            connection,
            "INSERT INTO member_org_relations "
            "(member_id, org_unit_id, relation_type, is_primary, valid_from, created_at, updated_at) "
            "VALUES (?, ?, 'STUDY_GROUP', 1, '2020-01-01', ?, ?)",
            (fixture["member_ids"]["moved"], fixture["group_ids"]["2组"], _now(), _now()),
        )
    expected_after_move = fetch_all(
        "SELECT org_unit_id, relation_type FROM member_org_relations "
        "WHERE member_id=? ORDER BY relation_type, org_unit_id",
        (fixture["member_ids"]["moved"],),
    )
    second = _import(fixture, [row], extra_headers=["备注"])
    observation = _observations(_batch_id(second))[0]
    assert observation["member_id"] == fixture["member_ids"]["moved"]
    assert observation["eligibility_reason"] == "GROUP_MISMATCH"
    assert observation["personal_credit_eligible"] == 0
    after = fetch_all(
        "SELECT org_unit_id, relation_type FROM member_org_relations "
        "WHERE member_id=? ORDER BY relation_type, org_unit_id",
        (fixture["member_ids"]["moved"],),
    )
    assert after == expected_after_move
    assert after != before
    assert fixture["group_ids"]["2组"] in [row["org_unit_id"] for row in after]
    assert _hq_fact_count(fixture) == 1


def test_unknown_and_missing_target_groups_are_blocked_without_auto_creation() -> None:
    fixture = _hq_fixture(
        specs=[{"key": "unknown", "name": "未知小组", "group": "班委组"}],
        group_names=("1组", "精进组", "班委组"),
    )
    before_group_count = int(
        fetch_one(
            "SELECT COUNT(*) AS n FROM org_units WHERE parent_id=? AND unit_type='GROUP'",
            (fixture["class_id"],),
        )["n"]
    )
    result = _import(
        fixture,
        [
            _row(name="未知小组", group="班委组"),
            _row(name="目标不存在组", group="99组", occurred_on="2026-03-03"),
        ],
    )
    identities = {(item["source_name"], item["source_group_name"]): item for item in result["identities"]}
    assert identities[("未知小组", "班委组")]["group_type"] == "UNKNOWN"
    assert identities[("未知小组", "班委组")]["mapped_group_org_unit_id"] == fixture["group_ids"]["班委组"]
    assert identities[("目标不存在组", "99组")]["reason"] == "GROUP_MAPPING_MISSING"
    _confirm(
        result,
        {("未知小组", "班委组", None): fixture["member_ids"]["unknown"]},
        names={"未知小组"},
    )
    observations = {row["source_name"]: row for row in _observations(_batch_id(result))}
    assert observations["未知小组"]["eligibility_reason"] == "GROUP_CLASSIFICATION_REQUIRED"
    assert observations["目标不存在组"]["match_status"] == "GROUP_MAPPING_MISSING"
    assert int(
        fetch_one(
            "SELECT COUNT(*) AS n FROM org_units WHERE parent_id=? AND unit_type='GROUP'",
            (fixture["class_id"],),
        )["n"]
    ) == before_group_count
    assert _hq_fact_count(fixture) == 0


def test_staff_without_learning_qualification_requires_review_but_learning_staff_can_credit() -> None:
    fixture = _hq_fixture(
        specs=[
            {"key": "review", "name": "工作人员待确认", "group": "1组"},
            {"key": "learning", "name": "工作人员学习", "group": "2组"},
        ]
    )
    result = _import(
        fixture,
        [
            _row(name="工作人员待确认", staff="是"),
            _row(name="工作人员学习", group="2组", staff="是", qualification="是"),
        ],
    )
    _confirm(
        result,
        {
            ("工作人员待确认", "1组", None): fixture["member_ids"]["review"],
            ("工作人员学习", "2组", None): fixture["member_ids"]["learning"],
        },
    )
    observations = {row["source_name"]: row for row in _observations(_batch_id(result))}
    assert observations["工作人员待确认"]["eligibility_reason"] == "REVIEW_REQUIRED"
    assert observations["工作人员学习"]["personal_credit_eligible"] == 1
    assert _hq_fact_count(fixture) == 1


def test_only_recording_field_controls_completion_and_dates_are_business_dates() -> None:
    content = _xlsx(
        [
            _row(name="提前", recording="提前完成"),
            _row(name="按时", recording="按时完成"),
            _row(name="过期", recording="过期完成"),
            _row(name="未提交", recording="未提交"),
            _row(name="短横线", recording="-"),
            _row(name="空值", recording=None),
            _row(name="备注不能替代", recording="-", 备注="按时完成"),
        ],
        extra_headers=["备注"],
    )
    parsed = parse_hq_reading_workbook(content, target_class_org_unit_id="class")
    statuses = {row["name"]: row["recording_status"] for row in parsed["rows"]}
    assert statuses["提前"] == "COMPLETE"
    assert statuses["按时"] == "COMPLETE"
    assert statuses["过期"] == "COMPLETE"
    assert statuses["未提交"] == "NOT_COMPLETE"
    assert statuses["短横线"] == "NOT_COMPLETE"
    assert statuses["空值"] == "NOT_COMPLETE"
    assert statuses["备注不能替代"] == "NOT_COMPLETE"
    assert all(row["occurred_on"] == "2026-03-02" for row in parsed["rows"])


def test_same_member_same_date_collapses_to_one_daily_credit_even_with_two_source_facts() -> None:
    fixture = _hq_fixture(
        specs=[
            {
                "key": "learner",
                "name": "同日两来源",
                "group": "1组",
                "masked_account": "138****4001",
                "last4": "4001",
            }
        ]
    )
    result = _import(
        fixture,
        [
            _row(name="同日两来源", account="138****4001"),
            _row(name="同日两来源", account="138****4999"),
        ],
    )
    _confirm(
        result,
        {
            ("同日两来源", "1组", "138****4001"): fixture["member_ids"]["learner"],
            ("同日两来源", "1组", "138****4999"): fixture["member_ids"]["learner"],
        },
    )
    assert _hq_fact_count(fixture) == 2
    calendar_id = _calendar(fixture, {"2026-03-02": "NORMAL_WORKDAY"})
    try:
        preview = dry_run_hq_reading_import(
            actor_user_id=_admin_id(), batch_id=_batch_id(result)
        )
    finally:
        _remove_calendar(calendar_id)
    ready = [item for item in preview["details"] if item["projected_status"] == "READY"]
    assert len(ready) == 2
    assert preview["daily_reading_preview"]["totals"]["proposed_entry_count"] == 1
    assert preview["daily_reading_preview"]["entries"][0]["source_fact_count"] == 2
    assert preview["daily_reading_preview"]["entries"][0]["points"] == 1.0


def test_exact_file_and_overlapping_date_file_are_idempotent() -> None:
    fixture = _hq_fixture(specs=[{"key": "learner", "name": "重复文件", "group": "1组"}])
    rows = [_row(name="重复文件")]
    original_content = _xlsx(rows)
    first = import_hq_reading_workbook(
        actor_user_id=_admin_id(),
        target_class_org_unit_id=str(fixture["class_id"]),
        original_filename=f"hq-{fixture['suffix']}.xlsx",
        content=original_content,
    )
    _confirm(first, {("重复文件", "1组", None): fixture["member_ids"]["learner"]})
    exact = import_hq_reading_workbook(
        actor_user_id=_admin_id(),
        target_class_org_unit_id=str(fixture["class_id"]),
        original_filename="same-file.xlsx",
        content=original_content,
    )
    assert exact["duplicate"] is True
    overlapping = _import(fixture, rows, extra_headers=["备注"])
    assert overlapping["duplicate"] is False
    assert int(fetch_one("SELECT COUNT(*) AS n FROM hq_reading_import_batches WHERE target_class_org_unit_id=?", (fixture["class_id"],))["n"]) == 2
    assert int(fetch_one("SELECT COUNT(*) AS n FROM hq_reading_import_observations WHERE source_type=? AND target_class_org_unit_id=?", (HQ_SOURCE_TYPE, fixture["class_id"]))["n"]) == 1
    assert _hq_fact_count(fixture) == 1
    observation = _observations(_batch_id(overlapping))[0]
    assert observation["seen_count"] == 2
    assert observation["first_batch_id"] == _batch_id(first)


def test_conflicting_duplicate_rows_are_retained_as_blocked_source_conflict() -> None:
    fixture = _hq_fixture(specs=[{"key": "learner", "name": "同源冲突", "group": "1组"}])
    result = _import(
        fixture,
        [
            _row(name="同源冲突", recording="按时完成"),
            _row(name="同源冲突", recording="未提交"),
        ],
    )
    observation = _observations(_batch_id(result))[0]
    assert observation["match_status"] == "SOURCE_IDENTITY_CONFLICT"
    assert observation["eligibility_reason"] == "SOURCE_FACT_CONFLICT"
    assert observation["seen_count"] == 1
    assert _hq_fact_count(fixture) == 0


def test_manual_excellent_share_uses_server_cap_and_deterministic_source_id() -> None:
    fixture = _hq_fixture(specs=[{"key": "learner", "name": "优秀分享", "group": "1组"}])
    for day in range(1, 7):
        created = record_manual_verified_excellent_share(
            actor_user_id=_admin_id(),
            member_id=fixture["member_ids"]["learner"],
            class_org_unit_id=str(fixture["class_id"]),
            occurred_on=f"2026-05-{day:02d}",
            note=f"运营核验{day}",
            evidence="班级报告会记录",
        )
        assert "points" not in created["metadata"]
    duplicate = record_manual_verified_excellent_share(
        actor_user_id=_admin_id(),
        member_id=fixture["member_ids"]["learner"],
        class_org_unit_id=str(fixture["class_id"]),
        occurred_on="2026-05-01",
        note="运营核验1",
        evidence="班级报告会记录",
    )
    assert duplicate["duplicate"] is True
    preview = dry_run_excellent_shares(
        actor_user_id=_admin_id(), class_org_unit_id=str(fixture["class_id"])
    )
    assert [entry["status"] for entry in preview["entries"]] == [
        "READY", "READY", "READY", "READY", "READY", "NO_CREDIT"
    ]
    assert preview["entries"][-1]["reasons"] == ["MONTHLY_CAP_REACHED"]
    assert preview["totals"]["proposed_points"] == 5.0
    assert preview["write_proof"]["ledger_entries_delta"] == 0


def test_aggregate_only_workbook_is_rejected_and_missing_target_is_not_guessed() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "个人完成率"
    sheet.append(["排名", "姓名", "应打卡天数", "已完成天数", "小组", "完成率", "按时完成", "提前完成", "过期完成", "未提交"])
    sheet.append([1, "汇总学员", 20, 18, "1组", 0.9, 10, 5, 3, 2])
    output = BytesIO()
    workbook.save(output)
    with pytest.raises(LearningCreditError, match="不能使用完成率等汇总列替代"):
        parse_hq_reading_workbook(output.getvalue(), target_class_org_unit_id="class")
    fixture = _hq_fixture()
    with pytest.raises(LearningCreditError, match="必须选择目标班级"):
        import_hq_reading_workbook(
            actor_user_id=_admin_id(),
            target_class_org_unit_id="",
            original_filename="empty-target.xlsx",
            content=_xlsx([_row(name="总部导入测试学员")]),
        )


def test_complete_source_fact_requires_controlled_hq_import_and_read_only_allows_only_dry_run() -> None:
    fixture = _hq_fixture()
    from app.services.learning_activity_credits import record_learning_activity_fact

    with pytest.raises(LearningCreditError, match="受控Excel导入入口"):
        record_learning_activity_fact(
            actor_user_id=_admin_id(),
            activity_type=DAILY_READING,
            member_id=fixture["member_ids"]["learner"],
            class_org_unit_id=str(fixture["class_id"]),
            occurred_on="2026-03-02",
            source_type=HQ_SOURCE_TYPE,
            source_id="bypass-attempt",
            participation_status="CONFIRMED",
            metadata={},
        )
    assert read_only_request_allowed("POST", "/api/v1/learning-credits/hq-reading/1/dry-run")
    assert not read_only_request_allowed("POST", "/api/v1/learning-credits/hq-reading/import")
    assert not read_only_request_allowed("POST", "/api/v1/learning-credits/hq-reading/1/confirm-identities")


def test_full_phone_is_rejected_before_batch_write() -> None:
    fixture = _hq_fixture()
    before = int(
        fetch_one(
            "SELECT COUNT(*) AS n FROM hq_reading_import_batches WHERE target_class_org_unit_id=?",
            (fixture["class_id"],),
        )["n"]
    )
    with pytest.raises(LearningCreditError, match="完整手机号"):
        _import(fixture, [_row(name="总部导入测试学员", account="13812345678")])
    after = int(
        fetch_one(
            "SELECT COUNT(*) AS n FROM hq_reading_import_batches WHERE target_class_org_unit_id=?",
            (fixture["class_id"],),
        )["n"]
    )
    assert before == after


def test_import_and_identity_confirmation_are_transactional_without_half_a_batch() -> None:
    fixture = _hq_fixture(
        specs=[
            {"key": "first", "name": "事务第一人", "group": "1组"},
            {"key": "second", "name": "事务第二人", "group": "2组"},
        ]
    )
    result = _import(
        fixture,
        [_row(name="事务第一人"), _row(name="事务第二人", group="2组")],
    )
    keys = {item["source_name"]: item["source_identity_key"] for item in result["identities"]}
    with pytest.raises(LearningCreditError, match="不能建立"):
        confirm_hq_reading_identities(
            actor_user_id=_admin_id(),
            batch_id=_batch_id(result),
            confirmations=[
                {"source_identity_key": keys["事务第一人"], "member_id": fixture["member_ids"]["first"]},
                {"source_identity_key": keys["事务第二人"], "member_id": 999999999},
            ],
        )
    assert int(fetch_one("SELECT COUNT(*) AS n FROM hq_reading_source_identities WHERE target_class_org_unit_id=?", (fixture["class_id"],))["n"]) == 0
    assert _hq_fact_count(fixture) == 0
    assert int(fetch_one("SELECT COUNT(*) AS n FROM hq_reading_import_observations WHERE batch_id=?", (_batch_id(result),))["n"]) == 2
