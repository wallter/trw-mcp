"""Tests for meta_tune.eval_gaming_detector — PRD-HPO-SAFE-001 FR-6 / NFR-5."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from trw_mcp.meta_tune.eval_gaming_detector import (
    EvalGamingVerdict,
    detect_eval_gaming,
)
from trw_mcp.models.config._main import TRWConfig
from trw_mcp.models.config._sub_models import MetaTuneConfig

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "meta_tune" / "dgm_attacks"


def _cfg_enabled() -> TRWConfig:
    return TRWConfig(meta_tune=MetaTuneConfig(enabled=True))


def _load_fixtures() -> list[dict[str, object]]:
    fixtures: list[dict[str, object]] = []
    for p in sorted(FIXTURE_DIR.glob("*.yaml")):
        fixtures.append(yaml.safe_load(p.read_text()))
    return fixtures


def test_fixtures_directory_has_five_attacks() -> None:
    fx = _load_fixtures()
    assert len(fx) >= 5, f"Expected ≥5 DGM attack fixtures, got {len(fx)}"


@pytest.mark.parametrize("fixture", _load_fixtures(), ids=lambda f: f["name"])
def test_detector_catches_all_fixtures(fixture: dict[str, object]) -> None:
    """NFR-5: detector MUST reject all synthetic DGM attacks (zero FN)."""
    proposal = fixture["proposal"]
    trace = fixture["outcome_trace"]
    verdict = detect_eval_gaming(
        diff=proposal["diff"],
        target_path=proposal["target_path"],
        outcome_trace=trace,
        _config=_cfg_enabled(),
    )
    assert verdict.rejected is True, f"Fixture {fixture['name']} not caught — verdict={verdict}"
    assert len(verdict.flags) >= 1


def test_detector_allows_clean_proposal() -> None:
    """A genuine advisory proposal with a varied trace and no tells is accepted."""
    diff = "--- a/CLAUDE.md\n+++ b/CLAUDE.md\n@@ -5,3 +5,4 @@\n Some advice.\n+Add a concrete example.\n"
    trace = [
        {"task": "t1", "score": 0.42},
        {"task": "t2", "score": 0.55},
        {"task": "t3", "score": 0.71},
        {"task": "t4", "score": 0.48},
        {"task": "t5", "score": 0.63},
    ]
    v = detect_eval_gaming(
        diff=diff,
        target_path="CLAUDE.md",
        outcome_trace=trace,
        _config=_cfg_enabled(),
    )
    assert v.rejected is False
    assert v.flags == ()


def test_detector_noop_when_disabled() -> None:
    """FR-7/FR-13: when kill-switch off, detector returns fail-safe reject."""
    v = detect_eval_gaming(diff="--- a/x\n+++ b/x\n", target_path="x", outcome_trace=[])
    assert v.disabled is True
    assert v.rejected is True


def test_eval_gaming_verdict_model_extra_forbid() -> None:
    from pydantic import ValidationError

    EvalGamingVerdict(rejected=False, flags=(), disabled=False)
    with pytest.raises(ValidationError):
        EvalGamingVerdict.model_validate({"rejected": False, "flags": [], "disabled": False, "extra": 1})
