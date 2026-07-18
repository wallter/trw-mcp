"""PRD-FIX-112: trw_prd_validate never-hang budget guard + fast mode.

Covers the cooperative wall-clock deadline threaded from the tool through
``refresh_dynamic_prd_validation`` into ``run_prd_integrity_checks``, the
``fast`` short-circuit, and the shared visibly-partial representation both
causes produce (``validation_partial`` + ``checks_skipped`` + a loud
``validation_partial:`` marker in ``integrity_warnings``).

The historical 20-min hang was already fixed (commit 2314a3fbb; largest PRDs
now refresh in 0.27-0.55s). These tests are the anti-regression guard: they
prove a future slowdown produces a *visibly partial* result rather than a hang
or a silent pass, so the gate can never be re-trained into bypass.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.models.requirements import ValidationResultV2
from trw_mcp.state.validation.prd_integrity import (
    INTEGRITY_CHECK_GROUPS,
    run_prd_integrity_checks,
)
from trw_mcp.state.validation.prd_quality import (
    DYNAMIC_CHECK_GROUPS,
    refresh_dynamic_prd_validation,
    validate_prd_quality_v2,
)

# A public-surface PRD with an unwired Must-Have FR — grounded checks have real
# work to do (wiring gate emits, repo path refs resolve), so skipping them is
# observable.
_PRD_UNWIRED_PUBLIC = """\
---
prd:
  id: PRD-TEST-777
  title: "Budget-guard fixture PRD"
  version: "1.0"
  status: draft
  priority: P1
  category: CORE

ip_tier: public
functionality_level: planned
stubs: []
---

# PRD-TEST-777: Budget-guard fixture

## 1. Problem Statement

A Must-Have public surface declares no `consumer:` / `wiring_test:` field and
the PRD carries no `seams:` block, so the dynamic wiring gate has real work.

## 3. Functional Requirements

### FR01 — Public surface with no consumer and no covering seam
**Priority**: Must Have

surface: public

This FR is a public surface with no wiring declaration and no seam coverage.
"""

# A functionality_level=live variant for the PRD-CORE-190 characterization test.
_PRD_LIVE_UNWIRED = """\
---
prd:
  id: PRD-TEST-778
  title: "Live PRD, unwired public surface"
  version: "1.0"
  status: draft
  priority: P1
  category: CORE

ip_tier: public
functionality_level: live
stubs: []
---

# PRD-TEST-778: Live PRD, unwired public surface

## 1. Problem Statement

A live (functionality_level=live) PRD whose Must-Have public FR declares no
wiring assertions — this test captures what the PRD-CORE-190 wiring gate emits.

## 3. Functional Requirements

### FR01 — Public surface, no consumer, no wiring_test
**Priority**: Must Have

surface: public

No `consumer:` / `wiring_test:` field and no `seams:` block.
"""


def _make_clock(low_calls: int, low: float = 0.0, high: float = 10_000.0) -> Callable[[], float]:
    """Return a fake ``time.monotonic`` returning *low* for the first
    ``low_calls`` invocations, then *high* for every subsequent call.

    Lets a test deterministically breach a deadline BETWEEN a chosen pair of
    cooperative check groups without relying on real wall-clock timing.
    """
    state = {"n": 0}

    def _clock() -> float:
        state["n"] += 1
        return low if state["n"] <= low_calls else high

    return _clock


def _base_result(content: str, config: TRWConfig) -> ValidationResultV2:
    """Pure (non-dynamic) base result, exactly as the tool caches it."""
    return validate_prd_quality_v2(content, config, include_dynamic_checks=False)


# ---------------------------------------------------------------------------
# Back-compat (FR03): default path is byte-identical + carries the new markers
# ---------------------------------------------------------------------------


def test_refresh_default_not_partial_and_score_identical_to_prechange(tmp_path: Path, config: TRWConfig) -> None:
    """No deadline / no fast == pre-change behavior: not partial, empty skip
    list, and a total_score identical to a refresh that never received the
    budget params at all (the unchanged code path)."""
    base = _base_result(_PRD_UNWIRED_PUBLIC, config)

    # Pre-change reference: refresh with NONE of the PRD-FIX-112 params.
    reference = refresh_dynamic_prd_validation(
        base.model_copy(deep=True),
        _PRD_UNWIRED_PUBLIC,
        config=config,
        project_root=str(tmp_path),
    )

    report: dict[str, object] = {}
    result = refresh_dynamic_prd_validation(
        base.model_copy(deep=True),
        _PRD_UNWIRED_PUBLIC,
        config=config,
        project_root=str(tmp_path),
        deadline=time.monotonic() + 3600.0,  # generous budget, never breached
        budget_report=report,
    )

    assert report == {"validation_partial": False, "checks_skipped": []}
    assert result.total_score == reference.total_score
    assert result.valid == reference.valid
    # No partial marker injected when nothing was skipped.
    assert not any(w.startswith("validation_partial:") for w in result.integrity_warnings)
    assert result.integrity_warnings == reference.integrity_warnings


# ---------------------------------------------------------------------------
# FR01: tiny budget -> partial shape, no hang
# ---------------------------------------------------------------------------


def test_refresh_tiny_budget_returns_partial_shape_no_hang(tmp_path: Path, config: TRWConfig) -> None:
    """An already-expired deadline skips every dynamic group and returns a
    visibly-partial result promptly (never raises, never hangs)."""
    base = _base_result(_PRD_UNWIRED_PUBLIC, config)
    report: dict[str, object] = {}

    started = time.monotonic()
    result = refresh_dynamic_prd_validation(
        base,
        _PRD_UNWIRED_PUBLIC,
        config=config,
        project_root=str(tmp_path),
        deadline=time.monotonic() - 1000.0,  # already in the past
        budget_report=report,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 5.0  # no hang
    assert report["validation_partial"] is True
    assert report["checks_skipped"] == list(DYNAMIC_CHECK_GROUPS)
    # Visibly partial: a loud leading marker naming the budget.
    marker = result.integrity_warnings[0]
    assert marker.startswith("validation_partial:")
    assert "prd_validate_budget_seconds" in marker


# ---------------------------------------------------------------------------
# FR01: deadline breach mid-checks skips only the REMAINING groups
# ---------------------------------------------------------------------------


def test_refresh_deadline_breach_skips_only_remaining_groups(
    tmp_path: Path, config: TRWConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the clock breaching after the first group's check, the first group
    RUNS and only the later groups are skipped (ordered cooperative skip)."""
    base = _base_result(_PRD_UNWIRED_PUBLIC, config)
    report: dict[str, object] = {}

    # First _budget_exhausted() call (group 1) sees low; every later call sees
    # high. deadline sits between them.
    monkeypatch.setattr(time, "monotonic", _make_clock(low_calls=1))

    result = refresh_dynamic_prd_validation(
        base,
        _PRD_UNWIRED_PUBLIC,
        config=config,
        project_root=str(tmp_path),
        deadline=100.0,
        budget_report=report,
    )

    assert report["validation_partial"] is True
    # Group 1 ran; groups 2-4 skipped, in order.
    assert report["checks_skipped"] == [
        "sprint_deferral",
        "integrity_checks",
        "wiring_gate",
    ]
    assert "dynamic_dimensions" not in report["checks_skipped"]
    assert result.integrity_warnings[0].startswith("validation_partial:")


@pytest.mark.parametrize(
    ("patch_target", "group"),
    [
        ("trw_mcp.state.validation.prd_quality.run_prd_integrity_checks", "integrity_checks"),
        ("trw_mcp.state.validation._prd_scoring_wiring.check_wiring_gate", "wiring_gate"),
    ],
)
def test_dynamic_check_exception_is_visibly_partial(
    tmp_path: Path,
    config: TRWConfig,
    monkeypatch: pytest.MonkeyPatch,
    patch_target: str,
    group: str,
) -> None:
    base = _base_result(_PRD_UNWIRED_PUBLIC, config)
    report: dict[str, object] = {}

    def fail_check(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("check unavailable")

    monkeypatch.setattr(patch_target, fail_check)
    result = refresh_dynamic_prd_validation(
        base,
        _PRD_UNWIRED_PUBLIC,
        config=config,
        project_root=str(tmp_path),
        budget_report=report,
    )

    assert report["validation_partial"] is True
    assert group in report["checks_skipped"]
    assert "dynamic validation check failure" in result.integrity_warnings[0]


def test_run_prd_integrity_checks_deadline_skips_remaining_subchecks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deadline threads INTO run_prd_integrity_checks: after two sub-checks
    the budget breaches and the remaining sub-checks are named as skipped."""
    from trw_mcp.state.prd_utils import parse_frontmatter

    frontmatter = parse_frontmatter(_PRD_UNWIRED_PUBLIC)
    skipped: list[str] = []

    # First two _budget_ok() calls run; the rest breach.
    monkeypatch.setattr(time, "monotonic", _make_clock(low_calls=2))

    failures, warnings = run_prd_integrity_checks(
        _PRD_UNWIRED_PUBLIC,
        frontmatter,
        project_root=tmp_path,
        prds_relative_path="docs/requirements-aare-f/prds",
        deadline=100.0,
        skipped=skipped,
    )

    # The last five groups (indices 2..6) were skipped, in order.
    assert skipped == list(INTEGRITY_CHECK_GROUPS[2:])
    # Never raises; still returns lists.
    assert isinstance(failures, list)
    assert isinstance(warnings, list)


# ---------------------------------------------------------------------------
# FR02: fast mode -> SAME partial representation, marker names fast mode
# ---------------------------------------------------------------------------


def test_refresh_fast_mode_partial_shape(tmp_path: Path, config: TRWConfig) -> None:
    """fast=True skips every dynamic group with the same partial shape as a
    budget breach, but the marker names fast mode (one representation, two
    causes)."""
    base = _base_result(_PRD_UNWIRED_PUBLIC, config)
    report: dict[str, object] = {}

    result = refresh_dynamic_prd_validation(
        base,
        _PRD_UNWIRED_PUBLIC,
        config=config,
        project_root=str(tmp_path),
        fast=True,
        budget_report=report,
    )

    assert report["validation_partial"] is True
    assert report["checks_skipped"] == list(DYNAMIC_CHECK_GROUPS)
    marker = result.integrity_warnings[0]
    assert marker.startswith("validation_partial:")
    assert "fast mode" in marker


# ---------------------------------------------------------------------------
# Tool-level (integration): fast, default, and a tiny injected budget
# ---------------------------------------------------------------------------


def _validate_tool() -> Any:
    from tests.conftest import extract_tool_fn, make_test_server

    return extract_tool_fn(make_test_server("requirements"), "trw_prd_validate")


def test_tool_fast_true_returns_partial_shape(tmp_path: Path) -> None:
    """trw_prd_validate(fast=True) returns a visibly-partial payload naming all
    dynamic groups as skipped."""
    fn = _validate_tool()
    prd = tmp_path / "PRD-TEST-777.md"
    prd.write_text(_PRD_UNWIRED_PUBLIC, encoding="utf-8")

    result = fn(prd_path=str(prd), fast=True)

    assert result["validation_partial"] is True
    assert result["checks_skipped"] == list(DYNAMIC_CHECK_GROUPS)
    assert any(w.startswith("validation_partial:") and "fast mode" in w for w in result["integrity_warnings"])


def test_tool_default_not_partial_and_fields_present(tmp_path: Path) -> None:
    """Default call: the two new fields are present and false/empty, and the
    total_score matches a direct pre-change refresh on the same PRD (FR03)."""
    fn = _validate_tool()
    prd = tmp_path / "PRD-TEST-777.md"
    prd.write_text(_PRD_UNWIRED_PUBLIC, encoding="utf-8")

    result = fn(prd_path=str(prd))

    assert result["validation_partial"] is False
    assert result["checks_skipped"] == []
    assert not any(w.startswith("validation_partial:") for w in result["integrity_warnings"])

    # Score parity with the unchanged (no budget params) code path.
    config = get_config()
    base = _base_result(_PRD_UNWIRED_PUBLIC, config)
    reference = refresh_dynamic_prd_validation(
        base,
        _PRD_UNWIRED_PUBLIC,
        config=config,
        project_root=str(tmp_path),
    )
    assert result["total_score"] == reference.total_score


def test_tool_tiny_budget_flags_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tiny configured budget makes the tool return a partial (budget) result
    rather than hanging — the anti-regression guard end to end."""
    fn = _validate_tool()
    prd = tmp_path / "PRD-TEST-777.md"
    prd.write_text(_PRD_UNWIRED_PUBLIC, encoding="utf-8")

    # Shrink the budget on the singleton the tool will read. A ~1us budget is
    # exceeded before the first cooperative check (real work happens between the
    # deadline computation and the first check), so every group is skipped.
    cfg = get_config()
    monkeypatch.setattr(cfg, "prd_validate_budget_seconds", 1e-6)

    result = fn(prd_path=str(prd))

    assert result["validation_partial"] is True
    assert result["checks_skipped"]  # non-empty
    marker = next(w for w in result["integrity_warnings"] if w.startswith("validation_partial:"))
    # Budget cause, not fast mode.
    assert "prd_validate_budget_seconds" in marker


# ---------------------------------------------------------------------------
# FR5c: perf smoke — a real large corpus PRD stays fast + non-partial under the
# default 60s budget (proves the guard does not itself impose latency, and that
# real PRDs never breach it).
# ---------------------------------------------------------------------------


def test_fr5c_perf_smoke_largest_corpus_prd_full_path_under_budget(tmp_path: Path) -> None:
    """The largest real corpus PRD, validated through the FULL tool path with
    the default 60s budget, completes well under a generous 10s wall-clock
    ceiling and is NOT partial (validation_partial False, checks_skipped [])."""
    # Repo root: tests -> trw-mcp -> trw-framework.
    repo_root = Path(__file__).resolve().parents[2]
    corpus_candidates = [
        repo_root / "docs" / "requirements-aare-f" / "prds" / "PRD-INFRA-056.md",
        repo_root / "docs" / "requirements-aare-f" / "prds" / "PRD-CORE-190-wiring-gate-prd-validate-seam-registry.md",
    ]
    corpus = next((p for p in corpus_candidates if p.exists()), None)
    if corpus is None:
        pytest.skip("no large corpus PRD available in this checkout")

    fn = _validate_tool()
    prd = tmp_path / corpus.name
    prd.write_text(corpus.read_text(encoding="utf-8"), encoding="utf-8")

    started = time.monotonic()
    result = fn(prd_path=str(prd))
    elapsed = time.monotonic() - started

    assert elapsed < 10.0, f"validate took {elapsed:.2f}s — regression vs the 60s budget"
    assert result["validation_partial"] is False
    assert result["checks_skipped"] == []


# ---------------------------------------------------------------------------
# FR6: wiring gate mode — block escalates an unwired public FR to a hard
# failure; warn keeps it advisory (valid unchanged by the gate).
# ---------------------------------------------------------------------------


def test_fr6_block_mode_unwired_live_prd_fails_full_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Under wiring_gate_mode='block', the unwired live PRD validated through the
    FULL tool path surfaces a WIRING_GATE_FAIL failure and valid is False."""
    fn = _validate_tool()
    prd = tmp_path / "PRD-TEST-778.md"
    prd.write_text(_PRD_LIVE_UNWIRED, encoding="utf-8")

    # Flip the gate to block on the singleton the tool reads; get_risk_scaled_config
    # copies the field through model_copy(update=...), so it reaches the gate.
    cfg = get_config()
    monkeypatch.setattr(cfg, "wiring_gate_mode", "block")

    result = fn(prd_path=str(prd))

    assert any(f["rule"] == "WIRING_GATE_FAIL" for f in result["failures"])
    assert result["valid"] is False


def test_fr6_warn_mode_unwired_live_prd_stays_valid_with_wiring_warning(tmp_path: Path, config: TRWConfig) -> None:
    """REQUIREMENT (was a characterization): in default warn mode the wiring gate
    is advisory — it emits a wiring_gate_warning but does NOT flip a
    would-be-valid result to invalid, and adds no WIRING_GATE_FAIL.

    Exercised at the dynamic-overlay layer (where wiring/integrity are the only
    checks that can flip ``valid``) from a clean valid=True base, so the
    assertion isolates the gate's contribution from unrelated V1 content gates.
    """
    from trw_mcp.state.validation import extract_wiring_warnings

    warn_config = config.model_copy(update={"wiring_gate_mode": "warn"})
    base = ValidationResultV2(valid=True)

    result = refresh_dynamic_prd_validation(
        base,
        _PRD_LIVE_UNWIRED,
        config=warn_config,
        project_root=str(tmp_path),
    )

    assert result.valid is True  # warn mode never blocks
    assert not any(f.rule == "WIRING_GATE_FAIL" for f in result.failures)
    assert any("wiring_gate_warning" in w and "FR01" in w for w in extract_wiring_warnings(result))


# ---------------------------------------------------------------------------
# Characterization (read-only): PRD-CORE-190 wiring gate on a live PRD lacking
# wiring assertions. Captures TODAY's actual behavior — the warn-mode advisory
# is now ALSO pinned as a requirement above
# (test_fr6_warn_mode_unwired_live_prd_stays_valid_with_wiring_warning) and the
# block-mode escalation in test_fr6_block_mode_unwired_live_prd_fails_full_validation.
# ---------------------------------------------------------------------------


def test_wiring_gate_live_prd_missing_wiring_characterization(tmp_path: Path) -> None:
    """CHARACTERIZATION (not a requirement): what does the PRD-CORE-190 wiring
    gate emit for a functionality_level=live PRD whose Must-Have public FR
    declares no consumer/wiring_test and no covering seam?

    Observed today: the gate WARNS (advisory ``wiring_gate_warning`` naming
    FR01) in default warn mode and adds NO ``WIRING_GATE_FAIL`` failure — i.e.
    functionality_level=live does NOT escalate the wiring gate to a hard block;
    the gate keys on surface/ip_tier, not functionality_level. If this changes,
    update this characterization (and PRD-CORE-190) deliberately.
    """
    from trw_mcp.state.prd_utils import parse_frontmatter
    from trw_mcp.state.validation._prd_scoring_wiring import check_wiring_gate

    frontmatter = parse_frontmatter(_PRD_LIVE_UNWIRED)
    assert str(frontmatter.get("functionality_level", "")) == "live"

    warnings, failures = check_wiring_gate(
        _PRD_LIVE_UNWIRED,
        frontmatter,
        mode="warn",
        project_root=tmp_path,
    )

    # Behavior captured: advisory warning, no hard failure.
    assert any("wiring_gate_warning" in w and "FR01" in w for w in warnings)
    assert not any(f.rule == "WIRING_GATE_FAIL" for f in failures)
