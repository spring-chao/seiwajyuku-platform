import sqlite3
from pathlib import Path

import pytest
from app.db import execute,fetch_one,transaction
from app.services.learning_requirement_policy import exemption_reason,requirement_roster
from test_v12_mvp import _seed_group_leader_fixture

@pytest.mark.parametrize('metric',['class_meeting','group_meeting','reading','sharing'])
@pytest.mark.parametrize('name',['炎武三班精进组',' 炎武三班 精进组 '])
def test_canonical_advanced_groups_are_exempt_for_every_requirement(metric,name):
    assert exemption_reason(name,'炎武三班',metric=metric)


@pytest.mark.parametrize('metric',['class_meeting','group_meeting','reading','sharing'])
@pytest.mark.parametrize('name',['精进组',' 精进组 ','精进同行组','一班精进组志愿队'])
def test_unprefixed_or_partial_advanced_names_are_normal_groups(metric,name):
    assert not exemption_reason(name,'炎武三班',metric=metric)


def test_bare_advanced_group_remains_in_requirement_roster():
    f=_seed_group_leader_fixture()
    with transaction() as c:
        execute(c,"UPDATE org_units SET name='精进组' WHERE id=?",(f['group_id'],))
        rows=requirement_roster(c,metric='group_meeting',class_id=f['class_id'])
        assert f['member_id'] in {r['member_id'] for r in rows}


def test_committee_exemption_is_only_for_group_meeting_not_every_class_duty():
    assert exemption_reason('班委组','合成一班',metric='group_meeting')
    assert not exemption_reason('班委组','合成一班',metric='class_meeting')
    assert not exemption_reason('班委组','合成一班',metric='reading')
    assert not exemption_reason('1精进同行组','合成一班',metric='group_meeting')


def test_roster_exemption_never_deletes_people_or_their_formal_relation():
    f=_seed_group_leader_fixture()
    before=fetch_one('SELECT COUNT(*) n FROM members')['n']
    with transaction() as c:
        execute(c,"UPDATE org_units SET name='合成班精进组' WHERE id=?",(f['group_id'],))
        rows=requirement_roster(c,metric='class_meeting',class_id=f['class_id'],include_ungrouped=True)
        assert f['member_id'] not in {r['member_id'] for r in rows}
        assert execute(c,"SELECT 1 FROM member_org_relations WHERE member_id=? AND org_unit_id=?",(f['member_id'],f['group_id'])).fetchone()
    assert fetch_one('SELECT COUNT(*) n FROM members')['n']==before


def test_sqlite_monthly_migration_replays_and_refuses_nonempty_reversal():
    root=Path(__file__).resolve().parents[3]
    up=(root/'migrations/sqlite/0073_study_meeting_monthly_roster.sql').read_text(encoding='utf-8-sig')
    down=(root/'migrations/rollback/sqlite/0073_study_meeting_monthly_roster.down.sql').read_text(encoding='utf-8-sig')
    c=sqlite3.connect(':memory:');c.execute('PRAGMA foreign_keys=ON')
    c.executescript('CREATE TABLE org_units(id TEXT PRIMARY KEY); CREATE TABLE members(id INTEGER PRIMARY KEY); CREATE TABLE schema_migrations(version TEXT PRIMARY KEY);')
    c.executescript(up);c.executescript(up);c.executescript(down);c.executescript(up)
    c.execute("INSERT INTO study_meeting_roster_months(period,freeze_due_at,state,created_at,updated_at) VALUES ('2026-10','2026-10-29 16:00:00','FROZEN','2026-10-29 16:00:00','2026-10-29 16:00:00')")
    c.commit()
    with pytest.raises(sqlite3.IntegrityError):c.executescript(down)
    assert c.execute('SELECT COUNT(*) FROM study_meeting_roster_months').fetchone()[0]==1
    assert c.execute("SELECT name FROM sqlite_master WHERE name='study_meeting_roster_members'").fetchone()
    c.close()
