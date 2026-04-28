"""Tests for PRD-CORE-152 task profile resolution."""

from __future__ import annotations

from trw_mcp.models.config import resolve_client_profile
from trw_mcp.models.run import ComplexityClass, ComplexitySignals
from trw_mcp.models.task_profile import resolve_task_profile


def test_resolve_task_profile_defaults_to_standard() -> None:
    profile = resolve_task_profile(client_profile=resolve_client_profile("claude-code"))

    assert profile.client_id == "claude-code"
    assert profile.complexity_class == "STANDARD"
    assert profile.ceremony_depth == "standard"
    assert profile.trace_depth == "standard"
    assert profile.exposed_tool_preset == "all"
    assert "VALIDATE" in profile.mandatory_phases
    assert len(profile.profile_hash) == 16


def test_light_client_keeps_validate_mandatory() -> None:
    profile = resolve_task_profile(
        client_profile=resolve_client_profile("codex"),
        complexity_class=ComplexityClass.MINIMAL,
        task_archetype="bugfix",
    )

    assert profile.client_id == "codex"
    assert profile.ceremony_depth == "light"
    assert profile.nudge_policy == "off"
    assert profile.trace_depth == "minimal"
    assert "VALIDATE" in profile.mandatory_phases
    assert "light ceremony preserves VALIDATE" in " ".join(profile.rationale)


def test_comprehensive_task_overrides_light_ceremony_depth() -> None:
    profile = resolve_task_profile(
        client_profile=resolve_client_profile("cursor-cli"),
        complexity_class=ComplexityClass.COMPREHENSIVE,
        task_archetype="feature",
    )

    assert profile.ceremony_depth == "comprehensive"
    assert profile.trace_depth == "causal"
    assert profile.nudge_policy == "dense"
    assert profile.exposed_tool_preset == "standard"


def test_task_profile_hash_changes_with_complexity() -> None:
    client = resolve_client_profile("claude-code")
    minimal = resolve_task_profile(client_profile=client, complexity_class=ComplexityClass.MINIMAL)
    standard = resolve_task_profile(client_profile=client, complexity_class=ComplexityClass.STANDARD)

    assert minimal.profile_hash != standard.profile_hash


def test_complexity_signals_are_classified() -> None:
    profile = resolve_task_profile(
        client_profile=resolve_client_profile("claude-code"),
        complexity_signals=ComplexitySignals(files_affected=8, architecture_change=True),
    )

    assert profile.complexity_class == "COMPREHENSIVE"
    assert profile.trace_depth == "causal"
