from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
import json
from pathlib import Path

import pytest

from app.db import execute, fetch_one, transaction
from app.services.learning_activity_credits import (
    DAILY_READING,
    EXCELLENT_SHARE,
    dry_run_daily_reading,
    dry_run_excellent_shares,
    get_business_calendar,
    record_learning_activity_fact,
    save_business_calendar,
)
from c7_1_1_parity import (
    EXPECTED_DAILY_READING_SIGNATURE,
    EXPECTED_EXCELLENT_SHARE_SIGNATURE,
    preview_parity_signature,
)
from test_learning_activity_credits import _admin_id, _fixture, _ledger_count


REPO_ROOT = Path(__file__).resolve().parents[3]
CALENDAR_CONFIG = (
    REPO_ROOT / "data" / "learning-calendars" / "china-mainland-2026.json"
)


@pytest.fixture(autouse=True)
def c71_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")


def _fact_count() -> int:
    row = fetch_one("SELECT COUNT(*) AS n FROM learning_credit_activity_facts")
    return int(row["n"])


def _persisted_preview_table_count() -> int:
    rows = fetch_one(
        "SELECT COUNT(*) AS n FROM sqlite_master "
        "WHERE type='table' AND name IN "
        "('learning_credit_proposals', 'learning_credit_staging')"
    )
    return int(rows["n"])


def _record_sample_fact(
    fixture: dict[str, int | str],
    *,
    activity_type: str,
    occurred_on: str,
    source_id: str,
) -> None:
    record_learning_activity_fact(
        actor_user_id=_admin_id(),
        activity_type=activity_type,
        member_id=int(fixture["member_id"]),
        class_org_unit_id=str(fixture["class_id"]),
        occurred_on=occurred_on,
        source_type="C7_1_ISOLATED_SAMPLE",
        source_id=source_id,
        participation_status="CONFIRMED",
        title="隔离环境业务形态样本（非生产数据）",
        metadata={"sample_scope": "isolated_test"},
        binding_id=int(fixture["binding_id"]),
    )


def test_official_2026_calendar_import_publish_and_c7_dry_run_are_zero_write() -> None:
    config = json.loads(CALENDAR_CONFIG.read_text(encoding="utf-8"))
    assert config["calendar_key"] == "CHINA_MAINLAND"
    assert config["calendar_year"] == 2026
    assert config["version_label"] == "2026.1"
    assert config["timezone"] == "Asia/Shanghai"
    assert config["status"] == "PUBLISHED"
    assert config["source"]["publisher"] == "国务院办公厅"
    assert config["source"]["document_number"] == "国办发明电〔2025〕7号"
    assert config["source"]["published_on"] == "2025-11-04"
    assert config["source"]["url"].startswith("https://www.gov.cn/")

    days = config["days"]
    assert len(days) == 365
    first_day = date(2026, 1, 1)
    expected_dates = {
        (first_day + timedelta(days=offset)).isoformat() for offset in range(365)
    }
    assert {item["business_date"] for item in days} == expected_dates
    counts = Counter(item["day_type"] for item in days)
    assert set(counts) == {
        "NORMAL_WORKDAY",
        "WEEKEND",
        "HOLIDAY",
        "ADJUSTED_WORKDAY",
    }
    assert counts == {
        "NORMAL_WORKDAY": 242,
        "WEEKEND": 84,
        "HOLIDAY": 33,
        "ADJUSTED_WORKDAY": 6,
    }

    fixture = _fixture()
    version_id: int | None = None
    try:
        imported = save_business_calendar(
            actor_user_id=_admin_id(),
            calendar_year=int(config["calendar_year"]),
            version_label=str(config["version_label"]),
            status="PUBLISHED",
            days=days,
        )
        version_id = int(imported["version"]["id"])
        assert imported["status"] == "PUBLISHED"
        assert imported["day_count"] == 365

        published = get_business_calendar(calendar_year=2026)
        assert published["status"] == "PUBLISHED"
        assert published["version"]["version_label"] == "2026.1"
        assert published["configured_day_count"] == 365
        assert Counter(item["day_type"] for item in published["days"]) == counts

        daily_samples = {
            "2026-01-05": "普通周一工作日",
            "2026-01-04": "元旦调休周日",
            "2026-02-14": "春节调休周六",
            "2026-01-01": "元旦法定节假日周四",
            "2026-01-11": "普通周日",
            "2027-01-01": "未配置下一年度日历",
        }
        for occurred_on, _label in daily_samples.items():
            _record_sample_fact(
                fixture,
                activity_type=DAILY_READING,
                occurred_on=occurred_on,
                source_id=f"daily-{occurred_on}",
            )
        for day in range(1, 7):
            _record_sample_fact(
                fixture,
                activity_type=EXCELLENT_SHARE,
                occurred_on=f"2026-05-{day:02d}",
                source_id=f"excellent-2026-05-{day:02d}",
            )

        ledger_before = _ledger_count()
        facts_before = _fact_count()
        daily = dry_run_daily_reading(
            actor_user_id=_admin_id(), class_org_unit_id=str(fixture["class_id"])
        )
        daily_entries = {entry["occurred_on"]: entry for entry in daily["entries"]}
        assert daily_entries["2026-01-05"]["status"] == "READY"
        assert daily_entries["2026-01-05"]["points"] == 1.0
        assert daily_entries["2026-01-04"]["status"] == "READY"
        assert daily_entries["2026-01-04"]["calendar"]["day_type"] == "ADJUSTED_WORKDAY"
        assert daily_entries["2026-02-14"]["status"] == "READY"
        assert daily_entries["2026-02-14"]["calendar"]["day_type"] == "ADJUSTED_WORKDAY"
        assert daily_entries["2026-01-01"]["status"] == "NO_CREDIT"
        assert daily_entries["2026-01-01"]["reasons"] == ["NOT_BUSINESS_WORKDAY"]
        assert daily_entries["2026-01-11"]["status"] == "NO_CREDIT"
        assert daily_entries["2027-01-01"]["status"] == "BLOCKED"
        assert daily_entries["2027-01-01"]["reasons"] == ["BUSINESS_CALENDAR_MISSING"]
        assert daily["totals"]["proposed_points"] == 3.0
        assert preview_parity_signature(daily) == EXPECTED_DAILY_READING_SIGNATURE
        assert daily["settlement_enabled"] is False
        assert daily["formal_settlement_allowed"] is False
        assert daily["write_proof"]["ledger_entries_delta"] == 0
        assert _ledger_count() == ledger_before
        assert _fact_count() == facts_before

        excellent = dry_run_excellent_shares(
            actor_user_id=_admin_id(), class_org_unit_id=str(fixture["class_id"])
        )
        excellent_statuses = [entry["status"] for entry in excellent["entries"]]
        assert excellent_statuses == ["READY"] * 5 + ["NO_CREDIT"]
        assert excellent["entries"][-1]["reasons"] == ["MONTHLY_CAP_REACHED"]
        assert excellent["totals"]["proposed_points"] == 5.0
        assert excellent["totals"]["proposed_entry_count"] == 5
        assert (
            preview_parity_signature(excellent)
            == EXPECTED_EXCELLENT_SHARE_SIGNATURE
        )
        assert excellent["settlement_enabled"] is False
        assert excellent["formal_settlement_allowed"] is False
        assert excellent["write_proof"]["ledger_entries_delta"] == 0
        assert _ledger_count() == ledger_before
        assert _fact_count() == facts_before
        assert _persisted_preview_table_count() == 0
    finally:
        with transaction() as connection:
            execute(
                connection,
                "DELETE FROM learning_credit_activity_facts "
                "WHERE source_type='C7_1_ISOLATED_SAMPLE'",
            )
            if version_id is not None:
                execute(
                    connection,
                    "DELETE FROM learning_business_calendar_versions WHERE id=?",
                    (version_id,),
                )
