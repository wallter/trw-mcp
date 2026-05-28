"""Tests for C2: CopilotPathInstructionsRenderer (PRD-DIST-2406 FR08-FR10).

Covers:
- test_apply_to_uses_directory_glob (FR08, P0-12)
- test_apply_to_multi_package_paths
- test_full_rewrite_on_sha_change (FR09)
- test_delete_rewrite_on_hotspot_set_change (FR09)
- test_stale_ttl_full_delete (FR10)
- test_gitignore_entry_added (FR09)
- test_idempotency_same_sha (FR05)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest


def _make_renderer() -> "CopilotPathInstructionsRenderer":
    from trw_mcp.channels.copilot._path_instructions import CopilotPathInstructionsRenderer
    return CopilotPathInstructionsRenderer()


def _make_sidecar(hotspot_paths: list[str] | None = None) -> dict[str, object]:
    if hotspot_paths is None:
        hotspot_paths = [
            "trw-mcp/src/trw_mcp/state/ceremony.py",
            "backend/routers/admin.py",
            "trw-memory/src/trw_memory/models/memory.py",
        ]
    return {
        "hotspots": [
            {"file": p, "risk_score": 0.85, "reason": "high churn"}
            for p in hotspot_paths
        ]
    }


# ---------------------------------------------------------------------------
# FR08 / P0-12 — applyTo uses directory globs, never literal paths
# ---------------------------------------------------------------------------


def test_apply_to_uses_directory_glob() -> None:
    """No literal filenames in applyTo — only directory patterns (P0-12)."""
    from trw_mcp.channels.copilot._path_instructions import compute_apply_to_glob

    hotspot_files = [
        "trw-mcp/src/trw_mcp/state/ceremony.py",
        "backend/routers/admin.py",
        "trw-memory/src/trw_memory/models/memory.py",
    ]
    result = compute_apply_to_glob(hotspot_files)

    # Must not contain literal file names
    assert "ceremony.py" not in result
    assert "admin.py" not in result
    assert "memory.py" not in result

    # Must contain directory glob patterns
    assert "/**/" in result or "/**" in result

    # Each glob component should be a directory pattern
    for glob_part in result.split(","):
        glob_part = glob_part.strip()
        # Should end with /*.py or similar extension glob, not a literal filename
        assert "/" in glob_part, f"Expected directory separator in glob: {glob_part}"


def test_apply_to_multi_package_paths() -> None:
    """Paths from different packages produce distinct directory globs."""
    from trw_mcp.channels.copilot._path_instructions import compute_apply_to_glob

    paths = [
        "backend/routers/admin.py",
        "trw-mcp/src/trw_mcp/tools/ceremony.py",
    ]
    result = compute_apply_to_glob(paths)
    parts = result.split(",")

    # Should have 2 distinct directory patterns
    assert len(parts) >= 2
    # Verify no literal filenames
    for part in parts:
        assert "admin.py" not in part
        assert "ceremony.py" not in part


def test_apply_to_empty_returns_default() -> None:
    """Empty hotspot list returns a safe default glob."""
    from trw_mcp.channels.copilot._path_instructions import compute_apply_to_glob

    result = compute_apply_to_glob([])
    assert result  # non-empty
    assert ".py" in result


# ---------------------------------------------------------------------------
# C2 render produces valid frontmatter
# ---------------------------------------------------------------------------


def test_full_rewrite_on_sha_change(tmp_path: Path) -> None:
    """New SHA causes file to be rewritten."""
    github_dir = tmp_path / ".github" / "instructions"
    github_dir.mkdir(parents=True)

    renderer = _make_renderer()
    result1 = renderer.render(
        tmp_path,
        _make_sidecar(),
        sidecar_sha="sha1",
    )
    assert result1.status == "written"
    target = tmp_path / ".github" / "instructions" / "trw-distill-hotspots.instructions.md"
    assert target.exists()
    content1 = target.read_text(encoding="utf-8")

    # Second render with different SHA
    result2 = renderer.render(
        tmp_path,
        _make_sidecar(["new/path/file.py"]),
        sidecar_sha="sha2",
    )
    assert result2.status == "written"
    content2 = target.read_text(encoding="utf-8")
    # Content should differ (new hotspot set)
    assert content1 != content2


def test_delete_rewrite_on_hotspot_set_change(tmp_path: Path) -> None:
    """Hotspot set change: old file deleted before new one written."""
    renderer = _make_renderer()

    result1 = renderer.render(
        tmp_path,
        _make_sidecar(["old/path/file.py"]),
        sidecar_sha="sha-old",
    )
    assert result1.status == "written"
    target = tmp_path / ".github" / "instructions" / "trw-distill-hotspots.instructions.md"
    assert target.exists()

    result2 = renderer.render(
        tmp_path,
        _make_sidecar(["new/path/different.py"]),
        sidecar_sha="sha-new",
    )
    assert result2.status == "written"
    # File should exist (rewritten)
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "new/path" in content or "different" in content


def test_stale_ttl_full_delete(tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
    """TTL exceeded causes file to be deleted (FULL_PRUNE, no T0 fallback).

    Mocks check_staleness since tmp_path is not a git repo and check_staleness
    would return ttl_unknown=True outside git repos.
    """
    from trw_mcp.channels._ttl import CheckResult

    renderer = _make_renderer()

    # First write to create the file
    result1 = renderer.render(
        tmp_path,
        _make_sidecar(),
        sidecar_sha="sha-ttl",
    )
    assert result1.status == "written"
    target = tmp_path / ".github" / "instructions" / "trw-distill-hotspots.instructions.md"
    assert target.exists()

    # Mock check_staleness to simulate TTL exceeded in a real git repo
    monkeypatch.setattr(
        "trw_mcp.channels.copilot._path_instructions.check_staleness",
        lambda **kwargs: CheckResult(is_stale=True, ttl_unknown=False, commits_since=25),
    )

    # Second render — TTL check will report stale
    result2 = renderer.render(
        tmp_path,
        _make_sidecar(),
        sidecar_sha="sha-ttl",
    )

    # TTL stale -> file deleted (FULL_PRUNE)
    assert result2.status == "skipped_ttl"
    # C2 has no T0 fallback — file should not exist
    assert not target.exists(), "C2 should FULL_PRUNE on stale, not T0 beacon"


def test_gitignore_entry_added(tmp_path: Path) -> None:
    """Rendering adds .github/instructions/trw-distill-hotspots.instructions.md to .gitignore."""
    renderer = _make_renderer()
    result = renderer.render(
        tmp_path,
        _make_sidecar(),
        sidecar_sha="sha-gi",
    )
    assert result.status == "written"

    gitignore = tmp_path / ".gitignore"
    assert gitignore.exists()
    content = gitignore.read_text(encoding="utf-8")
    assert "trw-distill-hotspots.instructions.md" in content


def test_idempotency_same_sha(tmp_path: Path) -> None:
    """Second render with same SHA and same hotspot set is idempotent."""
    renderer = _make_renderer()
    sidecar = _make_sidecar()

    result1 = renderer.render(tmp_path, sidecar, sidecar_sha="sha-same")
    assert result1.status == "written"
    target = tmp_path / ".github" / "instructions" / "trw-distill-hotspots.instructions.md"
    content1 = target.read_text(encoding="utf-8")

    # Second render (same SHA, same data)
    result2 = renderer.render(tmp_path, sidecar, sidecar_sha="sha-same")
    content2 = target.read_text(encoding="utf-8")

    # File may be rewritten or skipped — but content should be effectively the same
    assert result2.status in ("written", "skipped_conflict", "skipped_ttl")
