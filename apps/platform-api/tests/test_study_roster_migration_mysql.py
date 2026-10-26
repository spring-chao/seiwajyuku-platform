"""0073: actual MySQL DDL, idempotency and refusal to discard a captured month."""
import os
import re
from pathlib import Path
from uuid import uuid4

import pymysql
import pytest
from sqlalchemy.engine import make_url
from app.migrations import _split_mysql

pytestmark=pytest.mark.skipif(os.getenv('STUDY_ROSTER_ISOLATED_MYSQL')!='1',reason='explicit disposable MySQL only')
ROOT=Path(__file__).resolve().parents[3]

@pytest.fixture
def isolated():
    url=make_url(os.environ['DATABASE_URL'])
    assert os.environ['APP_ENV']=='test' and url.host=='127.0.0.1' and url.username=='b21_test' and url.database=='seiwajyuku_b21_test'
    schema='study_roster_ci_'+uuid4().hex[:12]
    assert re.fullmatch('study_roster_ci_[a-f0-9]{12}',schema)
    root=pymysql.connect(host='127.0.0.1',port=url.port or 3306,user='root',password=os.environ['STUDY_ROSTER_ISOLATED_ROOT_PASSWORD'],autocommit=True,cursorclass=pymysql.cursors.DictCursor)
    try:
        with root.cursor() as c:
            c.execute(f'CREATE DATABASE `{schema}` CHARACTER SET utf8mb4')
            c.execute(f'USE `{schema}`')
            c.execute('CREATE TABLE org_units(id VARCHAR(64) PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
            c.execute('CREATE TABLE members(id BIGINT PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4')
            c.execute('CREATE TABLE schema_migrations(version VARCHAR(64) PRIMARY KEY)')
        yield root
    finally:
        with root.cursor() as c:c.execute(f'DROP DATABASE IF EXISTS `{schema}`')
        root.close()


def apply(connection,path):
    with connection.cursor() as c:
        for statement in _split_mysql(path.read_text(encoding='utf-8-sig')):c.execute(statement)


def test_mysql_forward_twice_and_empty_reversal(isolated):
    up=ROOT/'migrations/mysql/0073_study_meeting_monthly_roster.sql'
    apply(isolated,up);apply(isolated,up)
    with isolated.cursor() as c:
        c.execute("SELECT COUNT(*) n FROM information_schema.referential_constraints WHERE constraint_schema=DATABASE()")
        assert c.fetchone()['n']==7
        c.execute("INSERT INTO schema_migrations(version) VALUES ('0073_study_meeting_monthly_roster.sql')")
    apply(isolated,ROOT/'migrations/rollback/mysql/0073_study_meeting_monthly_roster.down.sql')
    with isolated.cursor() as c:
        c.execute("SHOW TABLES LIKE 'study_meeting_roster_%'");assert not c.fetchall()
    apply(isolated,up)


def test_mysql_nonempty_reversal_stops_before_any_drop(isolated):
    apply(isolated,ROOT/'migrations/mysql/0073_study_meeting_monthly_roster.sql')
    with isolated.cursor() as c:
        c.execute("INSERT INTO org_units(id) VALUES ('synthetic-class'),('synthetic-group')")
        c.execute('INSERT INTO members(id) VALUES (1)')
        c.execute("INSERT INTO study_meeting_roster_months(period,freeze_due_at,state,frozen_at,created_at,updated_at) VALUES ('2026-10','2026-10-29 16:00:00','FROZEN','2026-10-29 16:00:00',UTC_TIMESTAMP(),UTC_TIMESTAMP())")
        c.execute("INSERT INTO study_meeting_roster_members(period,member_id,class_org_unit_id,group_org_unit_id,class_name,group_name,updated_at) VALUES ('2026-10',1,'synthetic-class','synthetic-group','合成班','合成组',UTC_TIMESTAMP())")
    with pytest.raises(pymysql.MySQLError):apply(isolated,ROOT/'migrations/rollback/mysql/0073_study_meeting_monthly_roster.down.sql')
    with isolated.cursor() as c:
        c.execute('SELECT COUNT(*) n FROM study_meeting_roster_members');assert c.fetchone()['n']==1
        c.execute("SELECT state FROM study_meeting_roster_months WHERE period='2026-10'");assert c.fetchone()['state']=='FROZEN'
