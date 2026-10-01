"""Retirement cleanup must cover every managed client skill root (REMOVE-S8a: disk-driven ``trw-*`` retirement)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from trw_mcp.bootstrap._version_migration import _cleanup_stale_artifacts

SKILL_ROOTS = (
    ".claude/skills",
    ".agents/skills",
    ".cursor/skills",
    ".github/skills",
    ".opencode/skills",
)


def _run(tmp_path: Path, hashes: dict[str, str]) -> dict[str, list[str]]:
    (tmp_path / ".trw").mkdir(exist_ok=True)
    (tmp_path / ".trw" / "managed-artifacts.yaml").write_text(
        yaml.safe_dump({"version": 2, "content_hashes": hashes}), encoding="utf-8"
    )
    result: dict[str, list[str]] = {"updated": [], "errors": []}
    _cleanup_stale_artifacts(tmp_path, result, None, manifest_hashes=hashes)
    return result


@pytest.mark.parametrize("relative_root", SKILL_ROOTS)
def test_retired_skill_removed_from_every_managed_client(tmp_path: Path, relative_root: str) -> None:
    retired = tmp_path / relative_root / "trw-review-pr"
    retired.mkdir(parents=True)
    (retired / "SKILL.md").write_text("retired", encoding="utf-8")

    result = _run(tmp_path, {f"{relative_root}/trw-review-pr/SKILL.md": hashlib.sha256(b"retired").hexdigest()})

    assert not retired.exists()
    assert not result.get("preserved")


@pytest.mark.parametrize("relative_root", SKILL_ROOTS)
def test_a_bare_name_is_never_retired(tmp_path: Path, relative_root: str) -> None:
    """Outside the ``trw-`` namespace a skill is the project's: recorded or not, it stays untouched."""
    bare = tmp_path / relative_root / "learn"
    bare.mkdir(parents=True)
    (bare / "SKILL.md").write_text("legacy", encoding="utf-8")

    result = _run(tmp_path, {f"{relative_root}/learn/SKILL.md": hashlib.sha256(b"legacy").hexdigest()})

    assert (bare / "SKILL.md").read_text(encoding="utf-8") == "legacy"
    assert result["updated"] == []
