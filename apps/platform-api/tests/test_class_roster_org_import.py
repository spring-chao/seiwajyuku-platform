from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.services.class_roster_org_import import (
    _ordinary_class_conflicts,
    _validate_preview,
    apply_confirmed_org_import,
    organization_topology,
)
from app.services.direct_class_preflight import DEVELOPMENT_CENTERS


def _confirmed_preview() -> dict:
    return {
        "source": {
            "active_member_count": 834,
            "with_class_count": 816,
            "missing_class_count": 18,
            "ordinary_class_member_count": 693,
            "direct_class_member_count": 123,
            "ordinary_class_count": 20,
            "direct_class_count": 4,
            "ordinary_group_pair_count": 112,
            "direct_group_pair_count": 11,
        },
        "matching": {
            "summary": [
                {"status": "MANUAL_REVIEW", "count": 28},
                {"status": "NO_PRODUCTION_MATCH", "count": 84},
                {"status": "UNIQUE_ACTIVE_MATCH", "count": 722},
            ]
        },
        "issues": [
            {"code": "DUPLICATE_SOURCE_PHONE", "count": 8},
            {"code": "INVALID_PHONE", "count": 9},
            {"code": "MISSING_CLASS", "count": 18},
            {"code": "MISSING_PHONE", "count": 11},
        ],
        "organization": {
            "root_match_count": 1,
            "development_center_match_counts": [
                {"center_name": center, "match_count": 1}
                for center in DEVELOPMENT_CENTERS
            ],
            "class_action_summary": [
                {"action": "CREATE_OR_RESOLVE", "count": 20},
                {"action": "REUSE", "count": 4},
            ],
            "group_action_summary": [
                {"action": "REUSE", "count": 11},
                {"action": "REVIEW", "count": 112},
            ],
        },
    }


class ClassRosterOrgImportTests(unittest.TestCase):
    def test_ordinary_class_conflict_is_active_and_parent_scoped(self) -> None:
        units = [
            {
                "unit_type": "CLASS",
                "name": "炎武一班",
                "is_active": 1,
                "parent_id": "sz-root",
            },
            {
                "unit_type": "CLASS",
                "name": "炎武一班",
                "is_active": 0,
                "parent_id": "kunshan-center",
            },
        ]

        self.assertEqual(
            _ordinary_class_conflicts(
                units, "炎武一班", "kunshan-center"
            ),
            [],
        )
        self.assertEqual(
            len(
                _ordinary_class_conflicts(
                    units, "炎武一班", "sz-root"
                )
            ),
            1,
        )

    def test_topology_is_parent_scoped_and_has_confirmed_counts(self) -> None:
        rows = []
        for class_index in range(20):
            class_name = f"普通班{class_index + 1:02d}"
            center = DEVELOPMENT_CENTERS[
                class_index % len(DEVELOPMENT_CENTERS)
            ]
            group_count = 6 if class_index < 12 else 5
            for group_index in range(group_count):
                rows.append(
                    {
                        "class_name": class_name,
                        "center_name": center,
                        "group_name": f"第{group_index + 1}组",
                    }
                )
        classes, groups = organization_topology(rows)

        self.assertEqual(len(classes), 20)
        self.assertEqual(len(groups), 112)
        self.assertIn(("普通班01", "第1组"), groups)
        self.assertIn(("普通班02", "第1组"), groups)

    def test_preview_gate_accepts_only_exact_production_aggregate(self) -> None:
        preview = _confirmed_preview()
        _validate_preview(preview)
        preview["matching"]["summary"][2]["count"] = 721
        with self.assertRaisesRegex(ValueError, "匹配汇总已变化"):
            _validate_preview(preview)

    def test_confirmation_and_source_fingerprint_fail_before_database_write(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "确认文字不匹配"):
            apply_confirmed_org_import(
                b"not-the-workbook", "latest.xlsx", "错误确认文字", 1
            )
        with self.assertRaisesRegex(ValueError, "工作簿指纹"):
            apply_confirmed_org_import(
                b"not-the-workbook",
                "latest.xlsx",
                "确认创建20个普通班和112个普通班小组",
                1,
            )

    def test_endpoint_is_closed_by_default_even_for_admin(self) -> None:
        with TestClient(app) as client:
            login = client.post(
                "/api/v1/auth/login",
                json={
                    "username": "admin",
                    "password": "test-admin-password",
                },
            )
            self.assertEqual(login.status_code, 200)
            token = login.json()["data"]["access_token"]
            response = client.post(
                "/api/v1/class-roster-org-import/apply",
                headers={"Authorization": f"Bearer {token}"},
                data={"confirmation_text": "任意文字"},
                files={
                    "workbook": (
                        "latest.xlsx",
                        b"not-the-workbook",
                        "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet",
                    )
                },
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "全量班级组织迁移开关未开启")


if __name__ == "__main__":
    unittest.main()
