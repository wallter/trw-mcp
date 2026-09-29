"""Client-neutral, low-drift AARE-F directory instructions."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
AAREF = ROOT / "docs" / "requirements-aare-f"

from tests._layout import MONOREPO_ROOT

if MONOREPO_ROOT is None:
    pytest.skip("monorepo-only AARE-F instruction invariant", allow_module_level=True)


def test_aaref_has_one_client_neutral_policy_owner() -> None:
    canonical = (AAREF / "AGENTS.md").read_text(encoding="utf-8")
    assert not (AAREF / "CLAUDE.md").exists()  # Claude Code reads AGENTS.md natively

    for marker in (
        "extra_prd_categories",
        "active and archived PRDs",
        "narrow the new PRD",
        "configured validation gates",
        "zero-context implementer",
        "functionality_level",
        "competitive-strategy.md",
    ):
        assert marker in canonical


def test_aaref_policy_omits_historical_and_fixed_runtime_lore() -> None:
    canonical = (AAREF / "AGENTS.md").read_text(encoding="utf-8")
    for stale in (
        "Sub-CLAUDE",
        "AARE-F v3.0.0",
        "completeness >85%",
        "traceability >90%",
        "FPI #",
        "cycle 59",
        "L-zYbV",
        "PRD-DIST-020",
        "/prd-new",
        "traceability-checker agent",
    ):
        assert stale not in canonical
