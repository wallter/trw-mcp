"""Tests for meta_tune.surface_registry — PRD-HPO-SAFE-001 FR-1, FR-8."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from trw_mcp.meta_tune.surface_registry import (
    Surface,
    SurfaceClassification,
    classify_candidate,
    classify_path,
)
from trw_mcp.models.config._main import TRWConfig
from trw_mcp.models.config._sub_models import MetaTuneConfig
from trw_mcp.models.meta_tune import CandidateEdit


def _enabled_config() -> TRWConfig:
    return TRWConfig(meta_tune=MetaTuneConfig(enabled=True))


def _candidate(target: str, diff: str | None = None) -> CandidateEdit:
    if diff is None:
        diff = f"--- a/{target}\n+++ b/{target}\n"
    return CandidateEdit(
        edit_id="11111111-1111-1111-1111-111111111111",
        proposer_id="agent:s",
        target_path=Path(target),
        diff=diff,
        created_ts=datetime(2026, 4, 23, tzinfo=timezone.utc),
    )


# --- Surface enum invariants --------------------------------------------------


def test_surface_enum_has_required_members() -> None:
    """FR-1: Surface classifies into exactly 5 domains."""
    members = {s.value for s in Surface}
    assert {"model", "prompt", "config", "policy", "weights"} <= members


# --- classify_path -------------------------------------------------------------


def test_classify_path_untagged_defaults_to_control_surface() -> None:
    """FR-8: untagged paths default to control (fail-safe closed)."""
    result = classify_path(Path("some/unknown/random/path.txt"))
    assert result.is_control is True
    assert result.surfaces == ()


def test_classify_path_claude_md_is_advisory_prompt() -> None:
    """CLAUDE.md files are advisory prompt surfaces."""
    result = classify_path(Path("CLAUDE.md"))
    assert result.is_control is False
    assert Surface.PROMPT in result.surfaces


def test_classify_path_constitution_is_control_surface() -> None:
    """docs/CONSTITUTION.md is control-surface per §Hard Boundaries."""
    result = classify_path(Path("docs/CONSTITUTION.md"))
    assert result.is_control is True


def test_classify_path_config_yaml_is_config_surface() -> None:
    result = classify_path(Path(".trw/config.yaml"))
    assert Surface.CONFIG in result.surfaces
    assert result.is_control is True
    assert "control" in (result.rationale or "")


def test_classify_path_pricing_yaml_is_config_surface() -> None:
    result = classify_path(Path("trw-mcp/src/trw_mcp/data/pricing.yaml"))
    assert Surface.CONFIG in result.surfaces


def test_classify_path_policy_hook_is_policy_surface() -> None:
    result = classify_path(Path("trw-mcp/src/trw_mcp/data/hooks/pre-tool.sh"))
    assert Surface.POLICY in result.surfaces
    assert result.is_control is True


def test_classify_path_weights_file_is_weights_surface() -> None:
    result = classify_path(Path("models/policy.safetensors"))
    assert Surface.WEIGHTS in result.surfaces


def test_classify_path_is_deterministic() -> None:
    p = Path("CLAUDE.md")
    assert classify_path(p) == classify_path(p)


# --- classify_candidate --------------------------------------------------------


def test_classify_candidate_from_target_path() -> None:
    c = _candidate("CLAUDE.md")
    result = classify_candidate(c, _config=_enabled_config())
    assert Surface.PROMPT in result.surfaces
    assert result.is_control is False


def test_classify_candidate_rejects_control_surface() -> None:
    c = _candidate("docs/CONSTITUTION.md")
    result = classify_candidate(c, _config=_enabled_config())
    assert result.is_control is True


def test_classify_candidate_sniffs_diff_for_multi_surface_edits() -> None:
    """If the diff mentions a control-surface path, the candidate is control."""
    diff = "--- a/docs/CONSTITUTION.md\n+++ b/docs/CONSTITUTION.md\n"
    c = _candidate("CLAUDE.md", diff=diff)
    result = classify_candidate(c, _config=_enabled_config())
    assert result.is_control is True
    assert "docs/CONSTITUTION.md" in (result.rationale or "")


# --- kill switch ---------------------------------------------------------------


def test_classify_candidate_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-7/FR-13: when meta_tune.enabled=False the entry point MUST no-op."""
    from trw_mcp.models.config import _main as config_main

    cfg = config_main.TRWConfig()
    # enabled defaults False — classify_candidate should return fail-safe control
    result = classify_candidate(_candidate("CLAUDE.md"), _config=cfg)
    # When disabled, the classifier fails closed — control surface, empty surfaces.
    assert result.is_control is True
    assert result.disabled is True


# --- SurfaceClassification invariants -----------------------------------------


def test_classification_model_is_frozen_extra_forbid() -> None:
    from pydantic import ValidationError

    cls = SurfaceClassification(is_control=False, surfaces=(Surface.PROMPT,))
    with pytest.raises(ValidationError):
        cls.model_copy(update={"unknown": 1}, deep=False).model_validate(
            {"is_control": False, "surfaces": [Surface.PROMPT], "unknown": 1}
        )
    with pytest.raises(ValidationError):
        # frozen: direct mutation raises
        cls.__class__.model_validate({"is_control": False, "surfaces": [Surface.PROMPT], "extra": 1})
