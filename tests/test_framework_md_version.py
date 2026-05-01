"""PRD-CORE-161 FR01/FR02: FRAMEWORK.md v25 verification."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CANONICAL = _REPO_ROOT / ".trw" / "frameworks" / "FRAMEWORK.md"
_BUNDLED = _REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "framework.md"
_ROOT = _REPO_ROOT / "FRAMEWORK.md"
_PACKAGE_ROOT = _REPO_ROOT / "trw-mcp" / "FRAMEWORK.md"


def _canonical_text() -> str:
    return _CANONICAL.read_text(encoding="utf-8")


def test_framework_version() -> None:
    text = _canonical_text()
    assert "v25_TRW" in text
    assert "Version date: 2026-04-30" in text
    assert "Model policy: capability-based" in text
    assert "v24.6_TRW" not in text


def test_provider_specific_cutover_removed() -> None:
    text = _canonical_text()
    for token in ("Opus 4.7", "OPUS-4-7-BEST-PRACTICES", "Claude Code Orchestrated", "GPT-class", "Claude-class", "Gemini-class", "Agent " + "Teams"):
        assert token not in text


def test_callout_within_execution_summary() -> None:
    head = "\n".join(_canonical_text().splitlines()[:20])
    assert "v25 mandate" in head
    assert "model prompt" in head


def test_framework_md_copies_match_canonical() -> None:
    assert _CANONICAL.exists(), f"missing canonical FRAMEWORK.md at {_CANONICAL}"
    canonical = _CANONICAL.read_bytes()
    for path in (_BUNDLED, _ROOT, _PACKAGE_ROOT):
        assert path.exists(), f"missing framework copy at {path}"
        assert path.read_bytes() == canonical, f"{path} drifted from {_CANONICAL}"
