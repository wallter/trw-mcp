"""PRD-CORE-149-FR01: verify the ``_static_sections`` facade preserves public API.

After the sub-package decomposition, every symbol that legacy callers imported
from ``trw_mcp.state.claude_md._static_sections`` MUST remain importable.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# Symbols publicly re-exported by ``_static_sections`` BEFORE and AFTER the
# decomposition. Additions to this list must be intentional.
_REQUIRED_PUBLIC_SYMBOLS = frozenset(
    {
        "_SESSION_BOUNDARY_TEXT",
        "_analytics_cache",
        "_format_learning_session_claim",
        "_load_analytics_counts",
        "_load_prompting_guide",
        "generate_behavioral_protocol_md",
        "render_agent_teams_protocol",
        "render_agents_trw_section",
        "render_behavioral_protocol",
        "render_ceremony_flows",
        "render_ceremony_quick_ref",
        "render_ceremony_table",
        "render_closing_reminder",
        "render_codex_instructions",
        "render_codex_trw_section",
        "render_delegation_protocol",
        "render_framework_reference",
        "render_imperative_opener",
        "render_memory_harmonization",
        "render_minimal_protocol",
        "render_opencode_instructions",
        "render_phase_descriptions",
        "render_rationalization_watchlist",
        "render_shared_learnings",
    }
)

# Module-level dependencies tests monkeypatch via
# ``monkeypatch.setattr(_static_sections, name, ...)``. The facade MUST expose
# these so patches propagate to every section module (which looks them up on
# the facade at call time).
_PATCHABLE_DEPENDENCIES = frozenset(
    {
        "FileStateReader",
        "MemoryConfig",
        "get_config",
        "list_org_shared_entries",
        "resolve_project_root",
        "time",
        "yaml",
    }
)


def test_public_api_preserved() -> None:
    """Every historically-public symbol is still importable from the facade."""
    from trw_mcp.state.claude_md import _static_sections

    exported = set(dir(_static_sections))
    missing = _REQUIRED_PUBLIC_SYMBOLS - exported
    assert not missing, f"facade dropped public symbols: {sorted(missing)}"


def test_patchable_dependencies_live_on_facade() -> None:
    """Dependency symbols used by section modules resolve on the facade.

    Section renderers call ``_facade.get_config()`` etc., so tests can still
    use ``monkeypatch.setattr(_static_sections, "get_config", ...)``.
    """
    from trw_mcp.state.claude_md import _static_sections

    exported = set(dir(_static_sections))
    missing = _PATCHABLE_DEPENDENCIES - exported
    assert not missing, (
        "facade must expose patchable dependency attributes for legacy "
        f"monkeypatch sites; missing: {sorted(missing)}"
    )


def test_render_functions_are_callable() -> None:
    """Smoke test: every render function imports and is callable."""
    from trw_mcp.state.claude_md import _static_sections

    # Sample 3 render functions. Full behavioral coverage lives in
    # test_protocol_renderer.py + test_claude_md_parity.py.
    for name in (
        "render_ceremony_quick_ref",
        "render_phase_descriptions",
        "render_closing_reminder",
    ):
        fn = getattr(_static_sections, name)
        assert callable(fn), f"{name} must be callable"
