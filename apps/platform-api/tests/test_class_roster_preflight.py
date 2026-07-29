from __future__ import annotations

import json
import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.services.class_roster_preflight import build_preflight


class FullClassRosterPreflightTests(unittest.TestCase):
    def test_build_preflight_is_aggregate_only_and_fail_closed(self) -> None:
        centers = (
            "园区分中心",
            "昆山分中心",
            "吴江分中心",
            "新吴分中心",
            "张家港分中心",
            "姑苏相城分中心",
        )
        org_units = [
            {
                "id": "root",
                "unit_code": "SZ_ROOT",
                "name": "苏州塾",
                "unit_type": "ROOT",
                "parent_id": None,
                "is_active": 1,
            },
            *[
                {
                    "id": f"center-{index}",
                    "unit_code": f"CENTER_{index}",
                    "name": name,
                    "unit_type": "REGIONAL_CENTER",
                    "parent_id": "root",
                    "is_active": 1,
                }
                for index, name in enumerate(centers, 1)
            ],
            {
                "id": "class-ordinary",
                "unit_code": "CLASS_ORDINARY",
                "name": "圆融一班",
                "unit_type": "CLASS",
                "parent_id": "center-1",
                "is_active": 1,
            },
            {
                "id": "class-pioneer",
                "unit_code": "CLASS_PIONEER",
                "name": "先锋班",
                "unit_type": "CLASS",
                "parent_id": "root",
                "is_active": 1,
            },
            {
                "id": "class-huangpu",
                "unit_code": "CLASS_HUANGPU",
                "name": "黄埔一班",
                "unit_type": "CLASS",
                "parent_id": "root",
                "is_active": 1,
            },
            {
                "id": "group-ordinary",
                "unit_code": "GROUP_ORDINARY",
                "name": "一组",
                "unit_type": "GROUP",
                "parent_id": "class-ordinary",
                "is_active": 1,
            },
            {
                "id": "group-huangpu",
                "unit_code": "GROUP_HUANGPU",
                "name": "拼搏组",
                "unit_type": "GROUP",
                "parent_id": "class-huangpu",
                "is_active": 1,
            },
        ]
        rows = [
            {
                "center_name": "园区分中心",
                "class_name": "圆融一班",
                "group_name": "一组",
                "phone_hash": "hash-1",
            },
            {
                "center_name": "昆山分中心",
                "class_name": "先锋班",
                "group_name": "先锋班",
                "phone_hash": "hash-2",
            },
            {
                "center_name": "吴江分中心",
                "class_name": "黄埔一班",
                "group_name": "拼搏组",
                "phone_hash": "hash-3",
            },
            {
                "center_name": "新吴分中心",
                "class_name": "",
                "group_name": "",
                "phone_hash": "hash-4",
            },
            {
                "center_name": "园区分中心",
                "class_name": "圆融一班",
                "group_name": "一组",
                "phone_hash": "",
            },
        ]
        members = [
            {
                "id": index,
                "phone_hash": f"hash-{index}",
                "status": "ACTIVE",
                "class_name": "",
                "group_name": "",
                "org_unit_id": "center-1",
                "development_org_unit_id": "center-1",
            }
            for index in (1, 2, 3)
        ]
        result = build_preflight(
            rows,
            org_units=org_units,
            members=members,
            relations=[],
            source_name="latest.xlsx",
            source_sha256="safe-fingerprint",
            sheet_name="2026 新在册表",
            source_issues=(
                {"code": "MISSING_CLASS", "count": 1},
                {"code": "INVALID_PHONE", "count": 1},
            ),
            note_only_group_count=1,
        )

        self.assertFalse(result["automatic_production_write_allowed"])
        self.assertEqual(result["source"]["active_member_count"], 5)
        self.assertEqual(result["source"]["missing_class_count"], 1)
        self.assertEqual(result["source"]["ordinary_class_count"], 1)
        self.assertEqual(result["source"]["direct_class_count"], 2)
        self.assertEqual(result["source"]["valid_group_pair_count"], 2)
        self.assertEqual(result["source"]["note_only_group_count"], 1)
        matching = {
            item["status"]: item["count"]
            for item in result["matching"]["summary"]
        }
        self.assertEqual(matching["UNIQUE_ACTIVE_MATCH"], 3)
        self.assertEqual(matching["NO_PRODUCTION_MATCH"], 1)
        self.assertEqual(matching["MANUAL_REVIEW"], 1)
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("hash-1", serialized)
        self.assertNotIn("phone_hash", serialized)
        self.assertNotIn("center-1", serialized)

    def test_endpoint_requires_authenticated_manager(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/class-roster-preflight/preview",
                files={
                    "workbook": (
                        "latest.xlsx",
                        b"not-a-workbook",
                        "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet",
                    )
                },
            )
        self.assertIn(response.status_code, {401, 403})


if __name__ == "__main__":
    unittest.main()
