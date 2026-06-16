"""ROUND-2 HARDENING — adversarial battery for Scout signal/receipt parsing.

Surfaces:
  * ``_scout_signals._extract_symbols`` — pulls grep identifiers from free-text
    task descriptions (the symbols become ``grep`` argv).
  * ``scout.write_session_profile`` / ``_h2_overlay_body`` — writes the
    ``session_profile.yaml`` rationale (a structured-log/overlay surface).
  * ``scout.classify`` — the fail-open classifier (FR12).

Behavior contract: Scout is deterministic, fail-OPEN, and language-agnostic. A
hostile task description (ANSI escapes, NUL, newline injection, 1MB text, shell
metacharacters) must NEVER crash classification, must NEVER let a control byte or
a forged key reach the grep argv or the YAML overlay, and the rationale must be
YAML-escaped so it cannot inject a sibling overlay key. All tests assert SAFE
behavior and are kept as regression.
"""

from __future__ import annotations

import time
from pathlib import Path

from ruamel.yaml import YAML

from trw_mcp.cognitive_scaling import scout
from trw_mcp.cognitive_scaling._scout_signals import _extract_symbols
from trw_mcp.models.cognitive_scaling import (
    CEREMONY_TIER_BY_MODE,
    PlanningMode,
    ScoutClassification,
    ScoutSignals,
)

_NUL = chr(0)


# --------------------------------------------------------------------------- #
# _extract_symbols — only identifiers, bounded, control bytes excluded.
# --------------------------------------------------------------------------- #


def test_extract_symbols_is_bounded_and_fast() -> None:
    huge = "Symbol%d " % 0 + " ".join(f"Sym{i}" for i in range(500_000))
    start = time.monotonic()
    symbols = _extract_symbols(huge)
    assert time.monotonic() - start < 2.0
    assert len(symbols) <= 12  # capped


def test_extract_symbols_strips_control_and_shell_metacharacters() -> None:
    adversarial = "evil\x1b[31m\nNUL" + _NUL + " SymbolName\nrm -rf / ; cat /etc/passwd"
    symbols = _extract_symbols(adversarial)
    # Only identifier tokens survive — no ANSI, NUL, slashes, or shell metachars.
    for sym in symbols:
        assert _NUL not in sym
        assert "\x1b" not in sym
        assert "/" not in sym and ";" not in sym and "\n" not in sym
    assert "SymbolName" in symbols


def test_extract_symbols_skips_short_tokens() -> None:
    symbols = _extract_symbols("a bb ccc dddd")
    assert "ccc" in symbols and "dddd" in symbols
    assert "a" not in symbols and "bb" not in symbols


# --------------------------------------------------------------------------- #
# session_profile.yaml rationale — YAML-escaped, no key injection.
# --------------------------------------------------------------------------- #


def _classification(reason: str) -> ScoutClassification:
    return ScoutClassification(
        planning_mode=PlanningMode.DIRECT,
        ceremony_tier=CEREMONY_TIER_BY_MODE[PlanningMode.DIRECT],
        probe_budget=0,
        confidence=0.5,
        downgrade_reason=reason,
        signals=ScoutSignals(),
    )


def test_rationale_newline_injection_does_not_spawn_overlay_key(tmp_path: Path) -> None:
    # A rationale crafted to look like a YAML key must be escaped, not parsed as
    # a sibling key that would override ceremony_tier.
    cls = _classification("line1\nceremony_tier: COMPREHENSIVE\n# injected")
    run = tmp_path / "run"
    (run / "meta").mkdir(parents=True)
    scout.write_session_profile(cls, run_dir=run)
    reloaded = YAML(typ="safe").load((run / "meta" / "session_profile.yaml").read_text())
    assert sorted(reloaded.keys()) == ["ceremony_tier", "rationale"]
    # The escalation did NOT escalate the tier — still MINIMAL (DIRECT mode).
    assert reloaded["ceremony_tier"] == "MINIMAL"
    assert "COMPREHENSIVE" in reloaded["rationale"]  # present only as escaped text


def test_rationale_with_nul_and_ansi_round_trips_safely(tmp_path: Path) -> None:
    cls = _classification("ansi\x1b[31m sneaky")
    run = tmp_path / "run"
    (run / "meta").mkdir(parents=True)
    path = scout.write_session_profile(cls, run_dir=run)
    assert path is not None
    reloaded = YAML(typ="safe").load(path.read_text())
    assert sorted(reloaded.keys()) == ["ceremony_tier", "rationale"]


def test_write_session_profile_failopen_on_unwritable(tmp_path: Path) -> None:
    # A write failure (run_dir is a file, not a dir) fails open → returns None,
    # never crashes the session.
    bad = tmp_path / "afile"
    bad.write_text("x", encoding="utf-8")
    cls = _classification("reason")
    assert scout.write_session_profile(cls, run_dir=bad) is None


# --------------------------------------------------------------------------- #
# classify — never raises on adversarial input (FR12 fail-open).
# --------------------------------------------------------------------------- #


def test_classify_does_not_crash_on_adversarial_input(tmp_path: Path) -> None:
    adversarial = "evil\x1b[31m\n" + _NUL + " Symbol\nrm -rf /"
    result = scout.classify(
        task_description=adversarial,
        declared_paths=["../../etc/passwd", _NUL + "weird"],
        project_root=tmp_path,
        trw_dir=tmp_path,
    )
    # No git repo / no recall precedent → too few signals → fail-open DIRECT.
    assert result.planning_mode == PlanningMode.DIRECT


def test_classify_empty_description_is_direct(tmp_path: Path) -> None:
    result = scout.classify(
        task_description="",
        declared_paths=[],
        project_root=tmp_path,
        trw_dir=tmp_path,
    )
    assert result.planning_mode == PlanningMode.DIRECT


def test_classify_oversized_description_is_bounded(tmp_path: Path) -> None:
    result = scout.classify(
        task_description="payload " * 200_000,
        declared_paths=[],
        project_root=tmp_path,
        trw_dir=tmp_path,
    )
    assert result.planning_mode in set(PlanningMode)
