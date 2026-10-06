from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import execute, transaction
from app.main import app
from app.services.wechat_learning import (
    LEARNING_CATEGORIES, _learning_type_name, _record_year, get_member_participation_history,
)
from test_v12_mvp import _seed_group_leader_fixture
from test_wechat_learning_summary import _bind, _insert_learning_facts, _stamp


@contextmanager
def _bound_client(fixture: dict):
    app_id = "participation-history-test"
    with patch.dict(os.environ, {
        "WECHAT_MEMBER_BINDING_ENABLED": "true",
        "WECHAT_MINIPROGRAM_APP_ID": app_id,
        "WECHAT_MINIPROGRAM_APP_SECRET": "participation-history-test-secret",
    }), patch(
        "app.services.wechat_identity.exchange_wechat_code",
        return_value={"appid": app_id, "openid": f"history-{fixture['suffix']}"},
    ), TestClient(app) as client:
        yield client, _bind(fixture, client)


def _fact(
    fixture: dict, activity_type: str, occurred_on: str, title: str,
    *, member_id: int | None = None, status: str = "COMPLETED",
) -> None:
    now = _stamp()
    with transaction() as connection:
        if not isinstance(connection, sqlite3.Connection):
            # The MySQL fact table stores a DATE, while SQLite keeps legacy
            # text timestamps as well. Preserve the calendar date in both.
            occurred_on = occurred_on[:10]
        batch = execute(
            connection,
            "INSERT INTO import_batches "
            "(import_type, source_name, source_sha256, status, preview_json, created_at) "
            "VALUES ('TEST', ?, ?, 'APPLIED', '{}', ?)",
            (f"history-{fixture['suffix']}", uuid4().hex, now),
        )
        execute(
            connection,
            "INSERT INTO member_activity_facts "
            "(source_system, source_table, external_id, member_id, org_unit_id, activity_type, "
            "occurred_on, participation_status, title, import_batch_id, imported_at) "
            "VALUES ('history-test', 'activity_workbook', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uuid4().hex, member_id or fixture["member_id"], fixture["class_id"],
             activity_type, occurred_on, status, title, int(batch.lastrowid), now),
        )


@pytest.mark.parametrize("activity_type,title,category", [
    ("LEARNING_MEETING", "2025年学习会", "学习会"),
    ("CLASS_STUDY_DAY", "班级日", "班级学习日"),
    ("CLASS_MEETING", "班会", "班级学习日"),
    ("CLASS_SESSION", "历史班会", "班级学习日"),
    ("GROUP_MEETING", "小组会", "小组学习会"),
    ("GROUP_SESSION", "历史小组会", "小组学习会"),
    ("CLASS_OPENING", "2025年开班", "开班"),
    ("COURSE", "经营课程", "课程"),
    ("学习会", "中文分类", "学习会"),
    ("OTHER", "六月小组学习会", "小组学习会"),
    ("OTHER", "六月班级学习日", "班级学习日"),
    ("OTHER_ACTIVITY", "开班仪式课程志工", "其他活动"),
    ("", "六月开班", "开班"),
    ("UNKNOWN", "六月课程", "课程"),
    ("BOARD_MEETING", "理事会学习会", "理事会"),
    ("REPORT_MEETING", "课程报告会", "报告会"),
    ("STUDY_TOUR", "课程游学", "游学"),
    ("READING_CHECKIN", "读书学习会打卡", "读书打卡"),
    ("VOLUNTEER_SERVICE", "课程志工", "志工活动"),
    ("STAFF_TRAINING", "课程培训", "培训活动"),
    ("NEW_UNKNOWN_TYPE", "未知学习会活动", "其他活动"),
])
def test_learning_classification_uses_explicit_five_category_definition(
    activity_type: str, title: str, category: str,
) -> None:
    assert _learning_type_name(activity_type, title) == category


@pytest.mark.parametrize("occurred_at,year", [
    ("2026-01-01T00:30:00+08:00", 2026),
    ("2025-12-31T23:30:00-08:00", 2025),
    ("2025-12-31", 2025), ("invalid", None), ("2025-02-30", None),
])
def test_history_year_uses_recorded_calendar_date(occurred_at: str, year: int | None) -> None:
    assert _record_year({"occurred_at": occurred_at}) == year


def test_history_is_session_scoped_private_and_separates_learning_from_activity() -> None:
    fixture = _seed_group_leader_fixture()
    _insert_learning_facts(fixture)
    # Additional attendance, study-meeting and historical rows for a different
    # member must not change the bound member's counts or organization names.
    unrelated = _seed_group_leader_fixture()
    _insert_learning_facts(unrelated)
    _fact(fixture, "STUDY_TOUR", "2024-09-20", "外班私有游学", member_id=fixture["foreign_member_id"])
    _fact(fixture, "COURSE", "2026-09-18", "缺席课程", status="ABSENT")
    _fact(fixture, "BOARD_MEETING", "2026-08-28T12:00:00+08:00", "班级月度学习会")
    with _bound_client(fixture) as (client, headers):
        assert client.get("/api/v1/wechat/participation-history").status_code == 401
        assert client.get(
            "/api/v1/wechat/participation-history", headers={"Authorization": "Bearer invalid"},
        ).status_code == 401
        response = client.get(
            f"/api/v1/wechat/participation-history?member_id={fixture['foreign_member_id']}",
            headers=headers,
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert [row["title"] for row in data["records"]] == [
            "班级月度学习会", "小组学习会 · 经营十二条",
        ]
        assert data["learning_count"] == data["total"] == 2
        assert data["activity_count"] == 1
        assert data["available_years"] == [2026]
        assert data["category_counts"]["班级学习日"] == 1
        assert data["category_counts"]["小组学习会"] == 1
        assert data["category_counts"]["读书分享"] == 1
        activities = client.get(
            "/api/v1/wechat/participation-history?kind=activity", headers=headers,
        ).json()["data"]
        assert [row["title"] for row in activities["records"]] == ["八月读书分享"]
        assert activities["total"] == 1
        assert activities["learning_count"] == 2
        for row in data["records"] + activities["records"]:
            assert set(row) == {
                "occurred_at", "learning_type", "title", "class_name", "group_name",
                "source_type", "status_name",
            }
        assert fixture["phone"] not in response.text
        assert "外班私有游学" not in response.text
        assert "source_id" not in response.text


def test_history_filters_calendar_year_and_counts_all_five_learning_categories() -> None:
    fixture = _seed_group_leader_fixture()
    for activity_type, title in [
        ("LEARNING_MEETING", "学习会"), ("CLASS_STUDY_DAY", "班级学习日"),
        ("GROUP_MEETING", "小组学习会"), ("CLASS_OPENING", "开班"), ("COURSE", "课程"),
    ]:
        _fact(fixture, activity_type, "2025-12-31", title)
    _fact(fixture, "COURSE", "2026-01-01T00:30:00+08:00", "新年课程")
    _fact(fixture, "STUDY_TOUR", "2026-12-31", "年末游学")
    with _bound_client(fixture) as (client, headers):
        data = client.get(
            "/api/v1/wechat/participation-history?year=2025", headers=headers,
        ).json()["data"]
        assert data["total"] == data["learning_count"] == 5
        assert data["activity_count"] == 0
        assert data["available_years"] == [2026, 2025]
        assert data["category_counts"] == dict.fromkeys(LEARNING_CATEGORIES, 1)
        data = client.get(
            "/api/v1/wechat/participation-history?year=2026", headers=headers,
        ).json()["data"]
        assert [row["title"] for row in data["records"]] == ["新年课程"]
        assert data["learning_count"] == data["activity_count"] == 1
        empty = client.get(
            "/api/v1/wechat/participation-history?year=2024", headers=headers,
        ).json()["data"]
        assert empty["records"] == []
        assert empty["learning_count"] == empty["activity_count"] == empty["total"] == 0
        assert empty["available_years"] == [2026, 2025]


def test_history_paginates_more_than_twenty_with_stable_order_and_summary_cap() -> None:
    fixture = _seed_group_leader_fixture()
    for index in range(27):
        _fact(fixture, "COURSE", "2026-06-01", f"课程{index:02d}")
    with _bound_client(fixture) as (client, headers):
        url = "/api/v1/wechat/participation-history?year=2026"
        first = client.get(url, headers=headers).json()["data"]
        repeated = client.get(url, headers=headers).json()["data"]
        second = client.get(url + "&page=2", headers=headers).json()["data"]
        beyond = client.get(url + "&page=3", headers=headers).json()["data"]
        assert first == repeated
        assert first["page"] == 1 and first["page_size"] == 20
        assert len(first["records"]) == 20 and first["has_more"] is True
        assert len(second["records"]) == 7 and second["has_more"] is False
        assert first["total"] == second["total"] == beyond["total"] == 27
        assert first["learning_count"] == first["category_counts"]["课程"] == 27
        assert len({row["title"] for row in first["records"] + second["records"]}) == 27
        assert beyond["records"] == [] and beyond["has_more"] is False
        summary = client.get("/api/v1/wechat/learning-summary", headers=headers).json()["data"]
        assert summary["recent_learning"] == first["records"]


@pytest.mark.parametrize("query", [
    "year=25", "year=2025.0", "year=2025-01-01", "year=10000", "year=1899",
    "year=02025", "year=2025%20", "year=2025%0A", "kind=all", "page=0", "page=-1",
    "page=1000001", "page_size=0", "page_size=51",
])
def test_history_rejects_invalid_query_parameters(query: str) -> None:
    with TestClient(app) as client:
        assert client.get(f"/api/v1/wechat/participation-history?{query}").status_code == 422


def test_history_returns_explicit_empty_state() -> None:
    fixture = _seed_group_leader_fixture()
    with _bound_client(fixture) as (client, headers):
        data = client.get("/api/v1/wechat/participation-history", headers=headers).json()["data"]
        assert data == {
            "records": [], "total": 0, "page": 1, "page_size": 20, "has_more": False,
            "available_years": [], "learning_count": 0, "activity_count": 0,
            "category_counts": dict.fromkeys(LEARNING_CATEGORIES, 0),
            "history_version": data["history_version"],
        }
        assert len(data["history_version"]) == 64
        int(data["history_version"], 16)
        client.post("/api/v1/wechat/member-bindings/revoke", headers=headers)
        assert client.get("/api/v1/wechat/participation-history", headers=headers).status_code == 401


def test_history_masks_phone_embedded_in_legacy_title() -> None:
    fixture = _seed_group_leader_fixture()
    _fact(fixture, "COURSE", "2025-08-01", f"课程联系 {fixture['phone']}")
    with _bound_client(fixture) as (client, headers):
        response = client.get("/api/v1/wechat/participation-history", headers=headers)
        assert response.status_code == 200
        assert fixture["phone"] not in response.text
        assert "****" in response.json()["data"]["records"][0]["title"]


def test_history_preserves_distinct_explicit_categories_on_same_day() -> None:
    fixture = _seed_group_leader_fixture()
    _fact(fixture, "COURSE", "2025-08-01", "八月班级活动")
    _fact(fixture, "CLASS_OPENING", "2025-08-01", "八月班级活动")
    data = get_member_participation_history(fixture["member_id"])
    assert data["total"] == data["learning_count"] == 2
    assert data["category_counts"]["课程"] == data["category_counts"]["开班"] == 1
    assert {row["learning_type"] for row in data["records"]} == {"课程", "开班"}


def test_history_version_detects_facts_changed_between_pages_without_exposing_ids() -> None:
    fixture = _seed_group_leader_fixture()
    for index in range(27):
        _fact(fixture, "COURSE", "2026-06-01", f"课程{index:02d}")
    with _bound_client(fixture) as (client, headers):
        url = "/api/v1/wechat/participation-history?year=2026"
        first = client.get(url, headers=headers).json()["data"]
        second = client.get(url + "&page=2", headers=headers).json()["data"]
        assert first["history_version"] == second["history_version"]
        assert len(first["history_version"]) == 64
        int(first["history_version"], 16)
        with transaction() as connection:
            execute(
                connection,
                "UPDATE member_activity_facts SET title='课程名称已更新' WHERE member_id=? AND title='课程00'",
                (fixture["member_id"],),
            )
        changed = client.get(url + "&page=2", headers=headers).json()["data"]
        assert changed["total"] == first["total"] == 27
        assert changed["history_version"] != first["history_version"]
        changed_first = client.get(url, headers=headers).json()["data"]
        assert changed["history_version"] == changed_first["history_version"]
        assert "课程名称已更新" in {
            row["title"] for row in changed_first["records"] + changed["records"]
        }
        _fact(fixture, "COURSE", "2026-07-01", "新课程")
        added = client.get(url + "&page=2", headers=headers).json()["data"]
        assert added["total"] == 28
        assert added["history_version"] != changed["history_version"]
        _fact(fixture, "COURSE", "2026-07-02", "他人课程", member_id=fixture["foreign_member_id"])
        unchanged = client.get(url + "&page=2", headers=headers).json()["data"]
        assert unchanged == added
        assert "source_id" not in unchanged
        assert fixture["phone"] not in json.dumps(unchanged)


def test_history_handles_missing_optional_tables_but_not_sql_errors() -> None:
    def _database(*, broken: bool = False):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE members (id INTEGER, status TEXT)")
        connection.execute("INSERT INTO members VALUES (1, 'ACTIVE')")
        if broken:
            connection.execute("CREATE TABLE org_units (id TEXT)")
            connection.execute("CREATE TABLE member_activity_facts (member_id INTEGER)")
        return connection

    with patch("app.services.wechat_learning.connect", side_effect=_database):
        data = get_member_participation_history(1)
        assert data["records"] == [] and data["available_years"] == []
    with patch("app.services.wechat_learning.connect", side_effect=lambda: _database(broken=True)):
        with pytest.raises(sqlite3.OperationalError, match="no such column"):
            get_member_participation_history(1)


def test_history_rejects_unavailable_member() -> None:
    fixture = _seed_group_leader_fixture()
    with transaction() as connection:
        execute(connection, "UPDATE members SET status='INACTIVE' WHERE id=?", (fixture["member_id"],))
    with pytest.raises(ValueError, match="身份不可用"):
        get_member_participation_history(fixture["member_id"])
