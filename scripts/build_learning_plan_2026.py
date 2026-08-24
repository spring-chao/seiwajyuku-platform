from __future__ import annotations

import argparse
import json
from pathlib import Path

from learning_plan_2026 import assert_valid_plan, build_standard_plan


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从三份 2026 学习计划 Excel 生成可审核的标准化 JSON"
    )
    parser.add_argument("--year1", required=True, type=Path, help="第一年正式学习计划工作簿")
    parser.add_argument("--year2", required=True, type=Path, help="第二年正式学习计划工作簿")
    parser.add_argument("--year3", required=True, type=Path, help="第三年正式学习计划工作簿")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/learning-plans/standard-3y-2026.json"),
        help="标准化 JSON 输出路径",
    )
    args = parser.parse_args()
    plan = build_standard_plan(args.year1, args.year2, args.year3)
    report = assert_valid_plan(plan)
    plan["quality_report"] = report
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"已生成: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
