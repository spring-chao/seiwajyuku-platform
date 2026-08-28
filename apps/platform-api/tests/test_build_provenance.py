import importlib.util
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "build_provenance.py"
SPEC = importlib.util.spec_from_file_location("build_provenance", SCRIPT_PATH)
assert SPEC and SPEC.loader
build_provenance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_provenance)


def test_r11_migration_manifest_validates_all_recovered_files():
    manifest = build_provenance.validate_migration_manifest(
        REPO_ROOT / "docs" / "V1.2-R1.1-migration-manifest-20260828.json",
        REPO_ROOT,
    )

    assert [item["version"] for item in manifest["migrations"]] == [
        "0035",
        "0036",
        "0037",
        "0038",
        "0039",
        "0040",
        "0041",
        "0042",
    ]


def test_manifest_cli_defaults_to_pending_without_explicit_ci_result():
    args = build_provenance._parser().parse_args(
        ["manifest", "--output", "release-manifest.json"]
    )

    assert args.ci_conclusion is None


def test_validate_manifest_rejects_pending_ci(tmp_path):
    path = tmp_path / "release-manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "release_commit": "a" * 40,
                "source_tree_sha": "b" * 40,
                "ci_conclusion": "pending",
                "github_ci_run": "123",
                "build_id": "123",
                "image_tag": "api:ci",
                "image_digest": None,
                "feature_flags": {},
                "migration_state": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="CI 尚未 success"):
        build_provenance.validate_manifest(path)
