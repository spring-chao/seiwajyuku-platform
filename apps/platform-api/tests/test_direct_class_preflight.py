from __future__ import annotations

import unittest

from app.services.direct_class_preflight import DEVELOPMENT_CENTERS, build_preflight
from app.main import read_only_request_allowed


class DirectClassProductionPreflightTests(unittest.TestCase):
    def test_read_only_mode_allows_only_the_ephemeral_preflight_post(self) -> None:
        self.assertTrue(
            read_only_request_allowed("POST", "/api/v1/auth/login")
        )
        self.assertTrue(
            read_only_request_allowed("POST", "/api/v1/auth/refresh")
        )
        self.assertTrue(
            read_only_request_allowed("POST", "/api/v1/direct-class-preflight/preview")
        )
        self.assertTrue(
            read_only_request_allowed("POST", "/api/v1/renewals/imports/preview")
        )
        self.assertTrue(
            read_only_request_allowed(
                "POST", "/api/v1/attendance/sync/scheduled"
            )
        )
        self.assertTrue(read_only_request_allowed("GET", "/api/v1/members"))
        self.assertFalse(read_only_request_allowed("POST", "/api/v1/members"))
        self.assertFalse(read_only_request_allowed("PUT", "/api/v1/metric-period-values"))

    def test_preflight_matches_only_hashed_identifiers_and_stays_read_only(self) -> None:
        org_units = [
            {"id": "org-suzhou", "unit_code": "SZ_ROOT", "name": "苏州塾", "unit_type": "ROOT", "parent_id": None, "is_active": 1},
            *[
                {"id": f"center-{index}", "unit_code": f"CENTER-{index}", "name": name, "unit_type": "REGIONAL_CENTER", "parent_id": "org-suzhou", "is_active": 1}
                for index, name in enumerate(DEVELOPMENT_CENTERS, start=1)
            ],
            {"id": "class-hp1", "unit_code": "HP1", "name": "黄埔一班", "unit_type": "CLASS", "parent_id": "org-suzhou", "is_active": 1},
            {"id": "class-hp2-wrong", "unit_code": "HP2", "name": "黄埔二班", "unit_type": "CLASS", "parent_id": "center-1", "is_active": 1},
        ]
        preview = build_preflight(
            [
                {"class_name": "黄埔一班", "center_name": "园区分中心", "phone_hash": "hash-a"},
                {"class_name": "黄埔二班", "center_name": "昆山分中心", "phone_hash": "hash-b"},
                {"class_name": "先锋班", "center_name": "新吴分中心", "phone_hash": "hash-c"},
                {"class_name": "神仙班", "center_name": "新吴分中心", "phone_hash": ""},
            ],
            org_units=org_units,
            members=[
                {"phone_hash": "hash-a", "class_name": "黄埔一班", "org_unit_id": "center-1", "development_org_unit_id": "center-1"},
                {"phone_hash": "hash-b", "class_name": "旧班级", "org_unit_id": "center-2", "development_org_unit_id": None},
            ],
            source_name="直属班级名单.xlsx",
            source_sha256="source-hash",
        )

        self.assertFalse(preview["automatic_production_write_allowed"])
        self.assertEqual(preview["organization"]["root_match_count"], 1)
        self.assertEqual(
            preview["organization"]["direct_class_status"],
            [
                {"class_name": "黄埔一班", "active_class_matches": 1, "correct_parent_matches": 1, "action": "REUSE"},
                {"class_name": "黄埔二班", "active_class_matches": 1, "correct_parent_matches": 0, "action": "CREATE_OR_RESOLVE"},
                {"class_name": "先锋班", "active_class_matches": 0, "correct_parent_matches": 0, "action": "CREATE_OR_RESOLVE"},
                {"class_name": "神仙班", "active_class_matches": 0, "correct_parent_matches": 0, "action": "CREATE_OR_RESOLVE"},
            ],
        )
        self.assertEqual(
            preview["matching"]["summary"],
            [
                {"status": "MANUAL_REVIEW", "count": 1},
                {"status": "NO_PRODUCTION_MATCH", "count": 1},
                {"status": "UNIQUE_PRODUCTION_MATCH", "count": 2},
            ],
        )
        self.assertEqual(
            preview["matching"]["no_production_match_by_class"],
            [{"class_name": "先锋班", "count": 1}],
        )
        self.assertNotIn("hash-a", str(preview))
        self.assertNotIn("hash-b", str(preview))


if __name__ == "__main__":
    unittest.main()
