"""Rendering tests for TRW surface area flags."""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig

# The eight ``resolve_surface`` tests that stood here were DELETED in 2.0.0 with
# the function (WD-02). Each monkeypatched ``get_config`` and asserted that
# ``resolve_surface("nudge")`` returned ``""`` or ``"__ENABLED__"`` -- a sentinel
# nothing in production ever received, because the resolver had no production
# call site. They were a full-coverage suite over a surface no surface used, and
# their green was the reason the dead projection looked maintained. The flags they
# nominally covered are asserted where they are actually READ: the ``render_*``
# tests below drive the real client-profile gates.


@pytest.mark.unit
def test_render_framework_ref_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """render_framework_reference() returns '' when include_framework_ref=False."""
    from trw_mcp.state.claude_md import _static_sections

    cfg = TRWConfig()
    patched_profile = cfg.client_profile.model_copy(update={"include_framework_ref": False})
    monkeypatch.setattr(
        _static_sections,
        "get_config",
        lambda: _MockConfigWithProfile(patched_profile),
    )

    from trw_mcp.state.claude_md._static_sections import render_framework_reference

    result = render_framework_reference()
    assert result == ""


@pytest.mark.unit
def test_render_framework_ref_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """render_framework_reference() returns content when include_framework_ref=True (default)."""
    from trw_mcp.state.claude_md import _static_sections

    cfg = TRWConfig()
    monkeypatch.setattr(_static_sections, "get_config", lambda: cfg)

    from trw_mcp.state.claude_md._static_sections import render_framework_reference

    result = render_framework_reference()
    assert "Framework Reference" in result
    assert "FRAMEWORK.md" in result


@pytest.mark.unit
def test_render_delegation_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """render_delegation_protocol() returns '' when include_delegation=False."""
    from trw_mcp.state.claude_md import _static_sections

    cfg = TRWConfig()
    patched_profile = cfg.client_profile.model_copy(update={"include_delegation": False})
    monkeypatch.setattr(
        _static_sections,
        "get_config",
        lambda: _MockConfigWithProfile(patched_profile),
    )

    from trw_mcp.state.claude_md._static_sections import render_delegation_protocol

    result = render_delegation_protocol()
    assert result == ""


@pytest.mark.unit
def test_render_delegation_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """render_delegation_protocol() returns content when include_delegation=True (default)."""
    from trw_mcp.state.claude_md import _static_sections

    cfg = TRWConfig()
    monkeypatch.setattr(_static_sections, "get_config", lambda: cfg)

    from trw_mcp.state.claude_md._static_sections import render_delegation_protocol

    result = render_delegation_protocol()
    assert "Delegation" in result


class _MockConfigWithProfile:
    """Minimal mock that exposes a client_profile property."""

    def __init__(self, profile: object) -> None:
        self._profile = profile

    @property
    def client_profile(self) -> object:
        return self._profile
