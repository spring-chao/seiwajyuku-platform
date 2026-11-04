import os
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlparse
import pytest
from app.migrations import MIGRATION_ROOT, _split_mysql


def test_sqlite_history_rebuild_preserves_schema_and_identifier_sequence():
    import sqlite3
    db=sqlite3.connect(':memory:')
    for p in sorted((MIGRATION_ROOT/'sqlite').glob('*.sql')):
        if p.name.startswith('0075'):break
        db.executescript(p.read_text(encoding='utf-8'))
    columns=list(db.execute('PRAGMA table_info(member_enrollment_applications)'))
    foreign_key_findings=list(db.execute('PRAGMA foreign_key_check'))
    db.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('member_enrollment_applications',1000)")
    db.commit()
    db.executescript((MIGRATION_ROOT/'sqlite/0075_enrollment_member_history.sql').read_text(encoding='utf-8'))
    assert list(db.execute('PRAGMA table_info(member_enrollment_applications)'))==columns
    assert db.execute("SELECT seq FROM sqlite_sequence WHERE name='member_enrollment_applications'").fetchone()[0]==1000
    assert list(db.execute('PRAGMA foreign_key_check'))==foreign_key_findings
    assert db.execute('PRAGMA foreign_keys').fetchone()[0]==1
    db.close()


def test_mysql_member_history_constraint_and_safe_recovery():
    if os.environ.get('ENROLLMENT_MIGRATION_ISOLATED_MYSQL') != '1':
        pytest.skip('Explicit disposable MySQL CI only')
    import pymysql
    url=urlparse(os.environ['DATABASE_URL'])
    assert os.environ['APP_ENV']=='staging' and url.hostname=='127.0.0.1' and url.path=='/seiwajyuku_staging'
    schema='enrollment_history_ci_'+uuid4().hex
    root=pymysql.connect(host='127.0.0.1',port=url.port or 3306,user='root',password=os.environ['ENROLLMENT_MIGRATION_ROOT_PASSWORD'],autocommit=True)
    try:
        with root.cursor() as c:
            c.execute(f'CREATE DATABASE `{schema}`')
            c.execute(f'USE `{schema}`')
            c.execute('CREATE TABLE members(id BIGINT PRIMARY KEY)')
            c.execute('CREATE TABLE member_enrollment_applications(id BIGINT PRIMARY KEY,converted_member_id BIGINT NULL UNIQUE, FOREIGN KEY(converted_member_id) REFERENCES members(id))')
            c.execute('INSERT INTO members VALUES(1)')
            c.execute('INSERT INTO member_enrollment_applications VALUES(1,1)')
            with pytest.raises(pymysql.err.IntegrityError):c.execute('INSERT INTO member_enrollment_applications VALUES(2,1)')
            for s in _split_mysql((MIGRATION_ROOT/'mysql/0075_enrollment_member_history.sql').read_text()):c.execute(s)
            c.execute('INSERT INTO member_enrollment_applications VALUES(2,1)')
            c.execute('SELECT COUNT(*) FROM member_enrollment_applications WHERE converted_member_id=1')
            assert c.fetchone()[0]==2
            with pytest.raises(pymysql.err.IntegrityError):c.execute('INSERT INTO member_enrollment_applications VALUES(3,999)')
            # Downgrade must refuse once the permitted one-to-many facts exist.
            with pytest.raises(pymysql.err.IntegrityError):c.execute('ALTER TABLE member_enrollment_applications ADD UNIQUE INDEX converted_member_id(converted_member_id)')
            c.execute('DELETE FROM member_enrollment_applications WHERE id=2')
            c.execute('ALTER TABLE member_enrollment_applications ADD UNIQUE INDEX converted_member_id(converted_member_id)')
            c.execute('ALTER TABLE member_enrollment_applications DROP INDEX idx_enrollment_converted_member')
            with pytest.raises(pymysql.err.IntegrityError):c.execute('INSERT INTO member_enrollment_applications VALUES(2,1)')
    finally:
        with root.cursor() as c:c.execute(f'DROP DATABASE `{schema}`')
        root.close()
