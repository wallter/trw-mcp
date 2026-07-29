"""PRD-CORE-161 FR01/FR02: FRAMEWORK version + copy parity (v26.1 refresh 2026-07-09)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Public-mirror guard: this test asserts a MONOREPO invariant (repo-root
# scripts/ + .trw/ layout) absent from the standalone trw-mcp PyPI/GitHub
# mirror. Skip cleanly there; the monorepo CI still enforces it.
if not (_REPO_ROOT / "scripts").is_dir():
    pytest.skip(
        "monorepo-only invariant (repo-root scripts/ absent in standalone mirror)",
        allow_module_level=True,
    )

_CANONICAL = _REPO_ROOT / ".trw" / "frameworks" / "FRAMEWORK.md"
_BUNDLED = _REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "framework.md"
_ROOT = _REPO_ROOT / "FRAMEWORK.md"
_PACKAGE_ROOT = _REPO_ROOT / "trw-mcp" / "FRAMEWORK.md"


def _canonical_text() -> str:
    return _CANONICAL.read_text(encoding="utf-8")


def test_framework_version() -> None:
    text = _canonical_text()
    assert "v26.2_TRW" in text
    # Assert the stamp is present and well-formed, not that it equals one past date.
    # A literal date here breaks on every legitimate version bump and teaches the next
    # editor to "fix" the test by pasting whatever the file now says -- which makes the
    # assertion a tautology. The binding that actually matters (canon header == config
    # default) is owned by check-aaref-sync.py.
    assert re.search(r"^Version date: \d{4}-\d{2}-\d{2}\b", text, re.MULTILINE), "no version date stamp"
    assert "Model policy: capability-based" in text
    assert "v24.6_TRW" not in text


def test_provider_specific_cutover_removed() -> None:
    text = _canonical_text()
    for token in (
        "Opus 4.7",
        "OPUS-4-7-BEST-PRACTICES",
        "Claude Code Orchestrated",
        "GPT-class",
        "Claude-class",
        "Gemini-class",
        "Agent " + "Teams",
    ):
        assert token not in text


def test_callout_within_execution_summary() -> None:
    head = "\n".join(_canonical_text().splitlines()[:20])
    assert "v26.2 mandate" in head
    assert "model prompt" in head


def test_framework_md_copies_match_canonical() -> None:
    assert _CANONICAL.exists(), f"missing canonical FRAMEWORK.md at {_CANONICAL}"
    canonical = _CANONICAL.read_bytes()
    for path in (_BUNDLED, _ROOT, _PACKAGE_ROOT):
        assert path.exists(), f"missing framework copy at {path}"
        assert path.read_bytes() == canonical, f"{path} drifted from {_CANONICAL}"
