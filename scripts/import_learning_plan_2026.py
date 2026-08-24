from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "apps" / "platform-api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from learning_plan_2026 import import_plan, validate_plan  # noqa: E402
from review_learning_plan_2026 import git_commit, verify_review_manifest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="将已通过 B1 校验的 2026 标准学习计划幂等导入为 DRAFT"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=REPO_ROOT / "data" / "learning-plans" / "standard-3y-2026.json",
        help="标准化 JSON 路径",
    )
    parser.add_argument("--apply", action="store_true", help="实际写入当前非生产数据库")
    parser.add_argument(
        "--no-replace-draft",
        action="store_true",
        help="已有 DRAFT 时拒绝重建；默认允许 DRAFT 幂等重建",
    )
    parser.add_argument(
        "--review-manifest",
        type=Path,
        default=REPO_ROOT / "data" / "learning-plans" / "standard-3y-2026.review.json",
        help="36项人工抽查的审核指纹清单；--apply 时必须为 CONFIRMED",
    )
    parser.add_argument(
        "--source-commit",
        help="固定审核提交；--apply 时必须与审核清单中的 source_commit 一致",
    )
    parser.add_argument("--actor-user-id", type=int, default=None)
    args = parser.parse_args()

    plan = json.loads(args.input.read_text(encoding="utf-8"))
    report = validate_plan(plan)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["errors"]:
        print("B1 校验未通过，禁止导入。", file=sys.stderr)
        return 2
    if not args.apply:
        print("仅校验完成；未写入数据库。需要显式指定 --apply 才会导入 DRAFT。")
        return 0

    # This script is intentionally unable to write production.  The normal
    # release path still requires a separately approved migration/write window.
    os.environ.setdefault("APP_ENV", "dev")
    from app.core.settings import get_settings

    settings = get_settings()
    if settings.is_production:
        raise RuntimeError("学习计划导入脚本禁止写入 production 数据库")

    if not args.source_commit:
        raise RuntimeError("--apply 必须显式提供固定审核提交 --source-commit")
    if not args.review_manifest.exists():
        raise RuntimeError("缺少36项人工抽查审核清单，禁止 B2 导入")
    review_manifest = json.loads(args.review_manifest.read_text(encoding="utf-8"))
    expected_commit = git_commit(REPO_ROOT, args.source_commit)
    verify_review_manifest(
        review_manifest,
        plan=plan,
        source_json=args.input,
        expected_source_commit=expected_commit,
        require_confirmed=True,
    )

    from app.db import transaction
    from app.migrations import run_migrations

    run_migrations()
    with transaction() as connection:
        result = import_plan(
            connection,
            plan,
            actor_user_id=args.actor_user_id,
            replace_draft=not args.no_replace_draft,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
