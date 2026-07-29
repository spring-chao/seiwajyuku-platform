from __future__ import annotations

import sys
import unittest
from pathlib import Path

# The preview command intentionally lives in the repository-level scripts folder.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.preview_direct_class_attribution import build_preview


class DirectClassPreviewTests(unittest.TestCase):
    def test_preview_keeps_learning_class_and_development_center_separate(self) -> None:
        preview = build_preview([
            {"是否在册": "在册", "所在分中心": "园区分中心", "所属班级": "先锋班", "所属小组": ""},
            {"是否在册": "在册", "所在分中心": "昆山分中心", "所属班级": "黄埔一班", "所属小组": "稻米组"},
            {"是否在册": "流失", "所在分中心": "园区分中心", "所属班级": "先锋班", "所属小组": ""},
        ])
        self.assertEqual(preview["direct_class_member_count"], 2)
        self.assertEqual(
            preview["class_center_matrix"],
            [
                {"class_name": "先锋班", "center": "园区分中心", "count": 1},
                {"class_name": "黄埔一班", "center": "昆山分中心", "count": 1},
            ],
        )
        self.assertEqual(preview["issues"], [])

    def test_preview_moves_pioneer_and_status_group_values_to_member_notes(self) -> None:
        preview = build_preview([
            {"是否在册": "在册", "所在分中心": "园区分中心", "所属班级": "先锋班", "所属小组": "诸队组"},
            {"是否在册": "在册", "所在分中心": "园区分中心", "所属班级": "黄埔二班", "所属小组": "目前不读书"},
        ])
        self.assertEqual(preview["issues"], [])
        self.assertEqual(
            preview["notes_to_preserve"],
            [
                {
                    "class_name": "先锋班",
                    "source_group_value": "诸队组",
                    "target_note": "原所属小组：诸队组",
                    "count": 1,
                },
                {
                    "class_name": "黄埔二班",
                    "source_group_value": "目前不读书",
                    "target_note": "原所属小组：目前不读书",
                    "count": 1,
                },
            ],
        )
        self.assertIn(
            {"class_name": "神仙班", "policy": "CONTRIBUTOR_FLEXIBLE_NO_REQUIREMENTS"},
            preview["class_policies"],
        )


if __name__ == "__main__":
    unittest.main()
