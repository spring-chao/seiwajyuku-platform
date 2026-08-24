from __future__ import annotations

import argparse
import json
from pathlib import Path

from learning_plan_2026 import PlanValidationError, validate_plan


def main() -> int:
    parser = argparse.ArgumentParser(description="校验标准化学习计划 JSON 的 B1 数据质量门禁")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/learning-plans/standard-3y-2026.json"),
        help="标准化 JSON 路径",
    )
    args = parser.parse_args()
    plan = json.loads(args.input.read_text(encoding="utf-8"))
    report = validate_plan(plan)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["errors"]:
        raise PlanValidationError(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
