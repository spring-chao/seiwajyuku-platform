"""Run unchanged on the local SQLite and isolated CI MySQL databases."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.db import execute, transaction
from app.services.learning_credits import _insert_entry
from app.services.wechat_credit_summary import get_member_credit_summary
from test_v12_mvp import _seed_group_leader_fixture


@pytest.fixture
def school():
    f = _seed_group_leader_fixture()
    base = datetime(2023, 1, 1, tzinfo=UTC)
    with transaction() as c:
        initial = execute(c, "SELECT * FROM class_learning_cycles WHERE id=?", (f['learning_cycle_id'],)).fetchone()
        binding = execute(c, "SELECT * FROM class_learning_bindings WHERE id=?", (initial['binding_id'],)).fetchone()
        execute(c, "UPDATE class_learning_bindings SET started_at=? WHERE id=?", (base.isoformat(), binding['id']))
        execute(c, "UPDATE class_learning_cycles SET opened_at=?,actual_class_meeting_at=NULL WHERE id=?", (base.isoformat(), initial['id']))
        ids = [int(initial['id'])]
        for index in range(2, 27):
            # Intentionally leave plan labels at year 1: real held days govern.
            plan = execute(c, "INSERT INTO learning_plan_cycles(plan_version_id,cohort_month,cycle_index,year_index,cycle_label,created_at,updated_at) VALUES (?,1,?,1,?, ?,?)",
                           (binding['plan_version_id'], index, f'test-{index}', base.isoformat(), base.isoformat()))
            opened = (base + timedelta(days=(index - 1) * 31)).isoformat()
            row = execute(c, "INSERT INTO class_learning_cycles(binding_id,class_org_unit_id,learning_cycle_index,plan_cycle_id,opened_at,class_meeting_status,group_meeting_policy,cycle_status,created_at,updated_at) VALUES (?,?,?, ?,?,'PLANNED','REQUIRED','OPEN',?,?)",
                          (binding['id'], f['class_id'], index, plan.lastrowid, opened, base.isoformat(), base.isoformat()))
            ids.append(int(row.lastrowid))
    return {**f, 'cycles': ids, 'base': base}


def held(f, count):
    with transaction() as c:
        for i, cid in enumerate(f['cycles']):
            actual = (f['base'] + timedelta(days=(i + 1) * 31)).isoformat() if i < count else None
            execute(c, "UPDATE class_learning_cycles SET class_meeting_status=?,actual_class_meeting_at=? WHERE id=?",
                    ('HELD' if actual else 'PLANNED', actual, cid))


def credit(f, cycle, points, *, member=None, original=None, status='POSTED'):
    with transaction() as c:
        return _insert_entry(c, {
            'member_id': member or f['member_id'], 'credit_category': 'STANDARD_LEARNING', 'credit_type': 'CLASS_MEETING_SCORE',
            'points': points, 'source_type': 'REVERSAL' if original else 'CLASS_MEETING', 'source_id': uuid4().hex,
            'class_org_unit_id': f['class_id'], 'learning_cycle_id': cycle,
            'rule_key': 'TEST', 'rule_version': 'TEST', 'rule_snapshot': {}, 'occurred_at': '2024-01-01',
            'idempotency_key': uuid4().hex, 'reversal_of_entry_id': original,
        }, status=status, actor_user_id=None)


def values(summary):
    return {row['year_index']: row['points'] for row in summary['learning_years']}


def test_twelve_actual_learning_days_start_new_year_at_zero_and_preserve_history(school):
    held(school, 11)
    credit(school, school['cycles'][11], '40.99')
    first = get_member_credit_summary(school['member_id'])
    assert first['current_learning_year'] == 1  # 26 planned months are not 26 held days.
    assert first['current_learning_year_points'] == '40.99'
    held(school, 12)
    second = get_member_credit_summary(school['member_id'])
    assert second['current_learning_year'] == 2
    assert second['current_learning_year_completed_days'] == 0
    assert second['current_learning_year_points'] == '0.00'
    assert values(second) == {1: '40.99', 2: '0.00', 3: '0.00'}
    credit(school, school['cycles'][12], '2.75')
    assert get_member_credit_summary(school['member_id'])['current_learning_year_points'] == '2.75'
    held(school, 24)
    third = get_member_credit_summary(school['member_id'])
    assert third['current_learning_year'] == 3 and third['current_learning_year_points'] == '0.00'
    assert values(third) == {1: '40.99', 2: '2.75', 3: '0.00'}


def test_reversal_stays_with_original_year_and_other_member_and_pending_are_excluded(school):
    held(school, 12)
    original = credit(school, school['cycles'][11], '10.99')
    # Original is marked REVERSED but both signed entries count toward its year.
    with transaction() as c:
        execute(c, "UPDATE learning_credit_entries SET status='REVERSED' WHERE id=?", (original['id'],))
    credit(school, None, '-10.99', original=original['id'])
    credit(school, school['cycles'][12], '99', member=school['foreign_member_id'])
    credit(school, school['cycles'][12], '77', status='PENDING')
    result = get_member_credit_summary(school['member_id'])
    assert values(result) == {1: '0.00', 2: '0.00', 3: '0.00'}
    assert not result['has_unallocated_learning_year_credits']


def test_unknown_historical_year_is_not_assigned_to_current_year(school):
    held(school, 12)
    credit(school, None, '20.50')
    result = get_member_credit_summary(school['member_id'])
    assert result['total_points'] == '20.50'
    assert result['current_learning_year_points'] == '0.00'
    assert result['has_unallocated_learning_year_credits']
    assert result['unallocated_learning_year_points'] == '20.50'


def test_unconfirmed_or_future_learning_days_do_not_advance_year(school):
    held(school, 11)
    with transaction() as c:
        execute(c, "UPDATE class_learning_cycles SET class_meeting_status='HELD',actual_class_meeting_at=? WHERE id=?",
                ((datetime.now(UTC) + timedelta(days=1)).isoformat(), school['cycles'][11]))
    result = get_member_credit_summary(school['member_id'])
    assert result['current_learning_year'] == 1
    assert result['current_learning_year_completed_days'] == 11
