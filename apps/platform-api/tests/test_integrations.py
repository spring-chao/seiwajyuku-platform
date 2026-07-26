from __future__ import annotations

import unittest
from datetime import UTC, datetime

from app.db import execute, fetch_all, fetch_one, transaction
from app.migrations import run_migrations
from app.services.iam import seed_iam
from app.services.integrations import (
    activity_admin_view,
    calculate_monthly_metrics,
    ingest_snapshots,
)


class IntegrationMetricTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        run_migrations()
        seed_iam()
        cls.admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            if not execute(
                connection, "SELECT id FROM org_units WHERE id='integration-center'"
            ).fetchone():
                execute(
                    connection,
                    "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, "
                    "created_at, updated_at) VALUES ('integration-center', 'INTEGRATION_CENTER', ?, "
                    "'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
                    ("集成测试中心", now, now),
                )
            cursor = execute(
                connection,
                "INSERT INTO annual_plans(year, version, policy_text, status, write_enabled, "
                "business_approval_reference, created_at, updated_at) "
                "VALUES (2031, 88, '集成自动指标测试', 'EXECUTING', 1, 'TEST-APPROVAL-ONLY', ?, ?)",
                (now, now),
            )
            cls.plan_id = cursor.lastrowid
            cls.versions = {}
            for order, (metric_key, name) in enumerate(
                [("reading_checkin_rate", "读书打卡率"), ("class_meeting_rate", "班会参与率")],
                1,
            ):
                definition = execute(
                    connection,
                    "SELECT id FROM metric_definitions WHERE metric_key=?", (metric_key,)
                ).fetchone()
                if not definition:
                    definition_id = execute(
                        connection,
                        "INSERT INTO metric_definitions(metric_key, name, category, default_unit, "
                        "created_at, updated_at) VALUES (?, ?, '测试', 'PERCENT', ?, ?)",
                        (metric_key, name, now, now),
                    ).lastrowid
                else:
                    definition_id = definition["id"]
                version_id = execute(
                    connection,
                    "INSERT INTO metric_versions(metric_definition_id, year, version, aggregation_type, "
                    "period_value_type, unit, data_source_type, null_policy, status, created_at) "
                    "VALUES (?, 2031, 88, 'WEIGHTED_AVG', 'MONTH', 'PERCENT', 'AUTO', "
                    "'NO_DATA', 'ACTIVE', ?)",
                    (definition_id, now),
                ).lastrowid
                execute(
                    connection,
                    "INSERT INTO plan_metrics(annual_plan_id, metric_version_id, display_order) "
                    "VALUES (?, ?, ?)",
                    (cls.plan_id, version_id, order),
                )
                cls.versions[metric_key] = version_id
            execute(
                connection,
                "INSERT INTO metric_period_values(annual_plan_id, org_unit_id, metric_version_id, "
                "period_type, period_no, value_kind, numeric_value, value_state, source_type, "
                "is_manual_override, updated_by, updated_at) "
                "VALUES (?, 'integration-center', ?, 'MONTH', 7, 'ACTUAL', 0.2, 'VALUE', "
                "'MANUAL', 1, ?, ?)",
                (cls.plan_id, cls.versions["reading_checkin_rate"], cls.admin["id"], now),
            )

    def test_idempotent_sync_auto_metric_and_manual_override(self) -> None:
        reading_event = {
                "external_id": "reading-001",
                "org_unit_id": "integration-center",
                "activity_type": "CHECKIN",
                "occurred_at": "2031-07-12T10:00:00+08:00",
                "eligible_count": 10,
                "completed_count": 8,
                "participant_phone": "13700137000",
                "title": "七月读书会",
            }
        attendance_event = {
                "external_id": "class-001",
                "org_unit_id": "integration-center",
                "activity_type": "CLASS_MEETING",
                "occurred_at": "2031-07-15T10:00:00+08:00",
                "eligible_count": 20,
                "completed_count": 15,
                "participant_phone": "13600136000",
                "title": "七月班会",
            }
        reading_first = ingest_snapshots(
            source_key="reading-test",
            snapshot_type="READING",
            api_key="dev-integration-key",
            events=[reading_event],
        )
        reading_second = ingest_snapshots(
            source_key="reading-test",
            snapshot_type="READING",
            api_key="dev-integration-key",
            events=[reading_event],
        )
        attendance_first = ingest_snapshots(
            source_key="signin-test",
            snapshot_type="ATTENDANCE",
            api_key="dev-integration-key",
            events=[attendance_event],
        )
        attendance_second = ingest_snapshots(
            source_key="signin-test",
            snapshot_type="ATTENDANCE",
            api_key="dev-integration-key",
            events=[attendance_event],
        )
        self.assertEqual(reading_first["inserted"], 1)
        self.assertEqual(reading_second["duplicates"], 1)
        self.assertEqual(attendance_first["inserted"], 1)
        self.assertEqual(attendance_second["duplicates"], 1)
        reading_rows = calculate_monthly_metrics(
            annual_plan_id=self.plan_id,
            source_key="reading-test",
            year=2031,
            month=7,
        )
        attendance_rows = calculate_monthly_metrics(
            annual_plan_id=self.plan_id,
            source_key="signin-test",
            year=2031,
            month=7,
        )
        indexed = {row["metric_key"]: row for row in reading_rows + attendance_rows}
        self.assertEqual(indexed["reading_checkin_rate"]["result"], "MANUAL_OVERRIDE_PRESERVED")
        self.assertEqual(indexed["class_meeting_rate"]["value"], 0.75)
        manual = fetch_one(
            "SELECT numeric_value FROM metric_period_values WHERE annual_plan_id=? "
            "AND metric_version_id=? AND period_no=7 AND value_kind='ACTUAL'",
            (self.plan_id, self.versions["reading_checkin_rate"]),
        )
        self.assertEqual(manual["numeric_value"], 0.2)
        snapshots = fetch_all("SELECT * FROM integration_snapshots")
        self.assertNotIn("13700137000", str(snapshots))
        self.assertNotIn("13600136000", str(snapshots))
        admin_rows = activity_admin_view(self.admin["id"], "2031-07")
        self.assertNotIn("participant_hash", admin_rows[0])

    def test_read_only_plan_rejects_metric_write(self) -> None:
        with transaction() as connection:
            execute(
                connection,
                "UPDATE annual_plans SET write_enabled=0, status='DRAFT' WHERE id=?",
                (self.plan_id,),
            )
        with self.assertRaises(PermissionError):
            calculate_monthly_metrics(
                annual_plan_id=self.plan_id,
                source_key="signin-test",
                year=2031,
                month=7,
            )
        with transaction() as connection:
            execute(
                connection,
                "UPDATE annual_plans SET write_enabled=1, status='EXECUTING' WHERE id=?",
                (self.plan_id,),
            )


if __name__ == "__main__":
    unittest.main()
