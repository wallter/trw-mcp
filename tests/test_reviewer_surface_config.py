"""PRD-SEC-015 FR02/FR14 — how the reviewer role is SELECTED, and by whom.

The role is process-scoped: a dispatched reviewer gets its own stdio server, so
the environment of that process is the only selector. A reviewer inspects a
repository it does not control, and that repository ships a ``.trw/config.yaml``
which the loader merges — so the hostile cases below are planted as REAL fixture
files and asserted by behaviour, never by reading the loader.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.surface_packs import REVIEWER_TOOLS


def _plant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_yaml: str | None) -> None:
    """Point the loader at a throwaway project (and an empty machine layer)."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    if config_yaml is not None:
        (tmp_path / ".trw").mkdir(exist_ok=True)
        (tmp_path / ".trw" / "config.yaml").write_text(config_yaml, encoding="utf-8")


def _fresh_config(monkeypatch: pytest.MonkeyPatch) -> object:
    from trw_mcp.models.config import get_config, reload_config

    reload_config()
    return get_config()


# ── FR02: the env selects the role; the default is agent ───────────────


def test_surface_role_env_override_and_admission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR02: TRW_SURFACE_ROLE selects the role, unset means agent, and the field
    carries a complete admission record (the CORE-218-FR05 gate)."""
    _plant(tmp_path, monkeypatch, config_yaml=None)

    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    assert _fresh_config(monkeypatch).surface_role == "reviewer"  # type: ignore[attr-defined]

    monkeypatch.delenv("TRW_SURFACE_ROLE")
    assert _fresh_config(monkeypatch).surface_role == "agent"  # type: ignore[attr-defined]

    from trw_mcp.models.config._defaults import verify_field_admissions
    from trw_mcp.models.config._main import TRWConfig

    report = verify_field_admissions(tuple(TRWConfig.model_fields.keys()))
    assert report.ok, f"unadmitted public fields: {report.missing}"
    assert "surface_role" not in report.missing


def test_the_surface_role_admission_records_that_the_role_dominates_everything() -> None:
    """The admission record is where a future maintainer learns that this field
    outranks tool_resolution_mode and task packs, and that setting it in a
    project config would bound every session in that project (RISK-008)."""
    from trw_mcp.models.config._defaults import build_field_admissions

    entry = build_field_admissions()["surface_role"]

    assert entry.owner == "PRD-SEC-015-FR02"
    assert "surface_authority" in entry.consumer
    assert entry.budget_decision == "admitted"
    assert "PRD-SEC-015" in entry.docs_pointer
    assert "test_reviewer_surface_config" in entry.test_pointer
    analysis = entry.interaction_analysis
    assert "tool_resolution_mode" in analysis and '"all"' in analysis
    assert "pack" in analysis
    assert "config.yaml" in analysis and "every session" in analysis


def test_a_bogus_role_is_rejected_rather_than_silently_becoming_reviewer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative: the Literal validator is the vocabulary gate. A typo must not
    resolve to a role at all — in either direction."""
    from pydantic import ValidationError

    from trw_mcp.models.config._main import TRWConfig

    _plant(tmp_path, monkeypatch, config_yaml=None)
    monkeypatch.setenv("TRW_SURFACE_ROLE", "bogus")

    with pytest.raises(ValidationError):
        TRWConfig()


# ── FR14: an audited repository cannot widen the lane auditing it ──────


def test_env_role_wins_over_project_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR14(a): a hostile project config cannot DOWNGRADE an env-declared role."""
    _plant(tmp_path, monkeypatch, config_yaml="surface_role: agent\n")
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")

    assert _fresh_config(monkeypatch).surface_role == "reviewer"  # type: ignore[attr-defined]


def test_a_project_config_alone_still_selects_the_role(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-vacuity for the test above: with no env var the SAME file IS read, so
    the previous assertion measures precedence rather than a dead key."""
    _plant(tmp_path, monkeypatch, config_yaml="surface_role: reviewer\n")
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)

    assert _fresh_config(monkeypatch).surface_role == "reviewer"  # type: ignore[attr-defined]


async def test_a_hostile_tool_resolution_mode_all_cannot_widen_a_reviewer_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR14(b), end-to-end through the REAL config: an audited repo declaring
    ``tool_resolution_mode: all`` does not un-bound the lane auditing it.

    Nothing is stubbed here — this is also the wiring proof that the middleware
    reads the live config field rather than a test double.
    """
    from dataclasses import dataclass
    from typing import Any

    from trw_mcp.middleware.surface_authority import SurfaceAuthorityMiddleware, reset_surface_authority_state
    from trw_mcp.server._surface_manifest_registry import eligible_tool_names

    @dataclass
    class _Ctx:
        @property
        def session_id(self) -> str:
            return "hostile-repo-sess"

    @dataclass
    class _Tool:
        name: str

    @dataclass
    class _MwCtx:
        message: Any = None
        fastmcp_context: Any = None

    _plant(tmp_path, monkeypatch, config_yaml="tool_resolution_mode: all\nsurface_role: agent\n")
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    _fresh_config(monkeypatch)
    reset_surface_authority_state()

    tools = [_Tool(name=n) for n in sorted(eligible_tool_names())]

    async def call_next(_ctx: Any) -> Any:
        return tools

    listed = await SurfaceAuthorityMiddleware().on_list_tools(_MwCtx(fastmcp_context=_Ctx()), call_next)  # type: ignore[arg-type]

    assert {t.name for t in listed} == set(REVIEWER_TOOLS)


def test_a_pre_change_config_still_loads_without_an_unrecognised_key_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Migration: a .trw/config.yaml written before this PRD is unaffected."""
    from structlog.testing import capture_logs

    _plant(tmp_path, monkeypatch, config_yaml="tool_resolution_mode: standard\ndebug: false\n")
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)

    with capture_logs() as logs:
        config = _fresh_config(monkeypatch)

    assert config.surface_role == "agent"  # type: ignore[attr-defined]
    assert not [entry for entry in logs if "unrecognised" in entry["event"] or "unrecognized" in entry["event"]], logs


# ── A misconfigured TRW_SURFACE_ROLE is logged, not silently swallowed ──


def test_unrecognized_surface_role_env_value_is_logged_once_per_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dispatch typo (e.g. ``TRW_SURFACE_ROLE=reviwer``) previously failed open
    with zero trace. It must now log a WARNING carrying the bad value, exactly
    once per process even across repeated resolution calls."""
    from structlog.testing import capture_logs

    from trw_mcp.middleware.surface_authority import _env_marks_reviewer, reset_surface_authority_state

    reset_surface_authority_state()
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviwer")

    with capture_logs() as logs:
        assert _env_marks_reviewer() is False
        assert _env_marks_reviewer() is False  # second call: no repeat warning

    warnings = [entry for entry in logs if entry["event"] == "surface_role_env_value_unrecognized"]
    assert len(warnings) == 1
    assert warnings[0]["value"] == "reviwer"


def test_recognized_surface_role_env_value_is_not_logged_as_unrecognized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-vacuity: case/whitespace-normalized 'reviewer' is the accepted
    sentinel and must never trip the unrecognized-value warning."""
    from structlog.testing import capture_logs

    from trw_mcp.middleware.surface_authority import _env_marks_reviewer, reset_surface_authority_state

    reset_surface_authority_state()
    monkeypatch.setenv("TRW_SURFACE_ROLE", "  Reviewer  ")

    with capture_logs() as logs:
        assert _env_marks_reviewer() is True

    assert not [entry for entry in logs if entry["event"] == "surface_role_env_value_unrecognized"]


def test_unset_surface_role_env_is_not_logged_as_unrecognized(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-vacuity: unset is the ordinary default path, not a misconfiguration."""
    from structlog.testing import capture_logs

    from trw_mcp.middleware.surface_authority import _env_marks_reviewer, reset_surface_authority_state

    reset_surface_authority_state()
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)

    with capture_logs() as logs:
        assert _env_marks_reviewer() is False

    assert not [entry for entry in logs if entry["event"] == "surface_role_env_value_unrecognized"]
