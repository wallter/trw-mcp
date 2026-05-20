from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from trw_mcp.code_index.models import (
    CODE_INDEX_SCHEMA_VERSION,
    CodeIndexFileRow,
    CodeIndexManifest,
    CodeIndexStats,
)


def test_code_index_manifest_requires_strict_core_fields() -> None:
    manifest = CodeIndexManifest(
        schema_version=CODE_INDEX_SCHEMA_VERSION,
        repo_root="/repo",
        git_head="a" * 40,
        generated_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
        files=[
            CodeIndexFileRow(
                path="src/app.py",
                sha256="b" * 64,
                size_bytes=12,
                indexed_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
            )
        ],
        stats=CodeIndexStats(
            total_files=1,
            added=1,
            unchanged=0,
            modified=0,
            deleted=0,
            skipped=0,
        ),
    )

    dumped = manifest.model_dump()

    assert dumped["schema_version"] == CODE_INDEX_SCHEMA_VERSION
    assert dumped["repo_root"] == "/repo"
    assert dumped["git_head"] == "a" * 40
    assert dumped["files"][0]["path"] == "src/app.py"
    assert dumped["stats"]["total_files"] == 1


def test_code_index_manifest_rejects_missing_extra_and_non_strict_values() -> None:
    valid = {
        "schema_version": CODE_INDEX_SCHEMA_VERSION,
        "repo_root": "/repo",
        "git_head": None,
        "generated_at": datetime(2026, 5, 20, tzinfo=timezone.utc),
        "files": [],
        "stats": {
            "total_files": 0,
            "added": 0,
            "unchanged": 0,
            "modified": 0,
            "deleted": 0,
            "skipped": 0,
        },
    }

    with pytest.raises(ValidationError):
        CodeIndexManifest.model_validate({**valid, "unexpected": True})

    missing_stats = dict(valid)
    del missing_stats["stats"]
    with pytest.raises(ValidationError):
        CodeIndexManifest.model_validate(missing_stats)

    with pytest.raises(ValidationError):
        CodeIndexStats.model_validate(
            {
                "total_files": "0",
                "added": 0,
                "unchanged": 0,
                "modified": 0,
                "deleted": 0,
                "skipped": 0,
            }
        )


def test_file_row_validates_sha_size_and_relative_path() -> None:
    indexed_at = datetime(2026, 5, 20, tzinfo=timezone.utc)

    CodeIndexFileRow(path="README.md", sha256="0" * 64, size_bytes=0, indexed_at=indexed_at)

    with pytest.raises(ValidationError):
        CodeIndexFileRow(path="/absolute.py", sha256="0" * 64, size_bytes=1, indexed_at=indexed_at)

    with pytest.raises(ValidationError):
        CodeIndexFileRow(path="../escape.py", sha256="0" * 64, size_bytes=1, indexed_at=indexed_at)

    with pytest.raises(ValidationError):
        CodeIndexFileRow(path="x.py", sha256="not-a-sha", size_bytes=1, indexed_at=indexed_at)
