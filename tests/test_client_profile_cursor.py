"""Cursor and ceremony-mode client profile tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig, resolve_client_profile
from trw_mcp.models.config._client_profile import WriteTargets
from trw_mcp.state.analytics.report import compute_ceremony_score


@pytest.mark.unit
def test_bare_cursor_id_falls_back_with_actionable_message() -> None:
    """resolve_client_profile('cursor') logs both replacement identifiers."""
    from trw_mcp.models.config._profiles import resolve_client_profile as _resolve

    mock_logger = MagicMock()
    with patch("trw_mcp.models.config._profiles.logger", mock_logger):
        profile = _resolve("cursor")

    assert profile.client_id == "claude-code"
    mock_logger.warning.assert_called_once()
    call_kwargs = mock_logger.warning.call_args
    assert call_kwargs.args[0] == "unknown_client_id_fallback"
    message = call_kwargs.kwargs.get("message", "")
    assert "cursor-ide" in message
    assert "cursor-cli" in message


@pytest.mark.unit
def test_cursor_ide_profile_ceremony_mode_full() -> None:
    """cursor-ide profile has ceremony_mode='full'."""
    profile = resolve_client_profile("cursor-ide")
    assert profile.ceremony_mode == "full"
    assert profile.tool_exposure_mode == "all"
    assert profile.nudge_enabled is True
    assert profile.learning_recall_enabled is True
    assert profile.mcp_instructions_enabled is True
    assert profile.skills_enabled is True


@pytest.mark.unit
def test_cursor_ide_write_targets_set_correctly() -> None:
    """cursor-ide write_targets has cursor_rules=True and agents_md=True."""
    profile = resolve_client_profile("cursor-ide")
    assert profile.write_targets.cursor_rules is True
    assert profile.write_targets.agents_md is True
    assert profile.write_targets.instruction_path == ".cursor/rules/trw-ceremony.mdc"


@pytest.mark.integration
def test_opencode_target_activates_light_mode() -> None:
    """Setting target_platforms=['opencode'] activates light ceremony mode."""
    config = TRWConfig(target_platforms=["opencode"])
    assert config.effective_ceremony_mode == "light"
    assert config.client_profile.ceremony_mode == "light"
    assert config.client_profile.agents_md_enabled is True
    assert config.client_profile.include_framework_ref is False


@pytest.mark.integration
def test_effective_ceremony_mode_explicit_light_overrides_profile() -> None:
    """Explicitly setting ceremony_mode='light' takes precedence over profile."""
    config = TRWConfig(ceremony_mode="light", target_platforms=["claude-code"])
    assert config.effective_ceremony_mode == "light"
    assert config.client_profile.ceremony_mode == "full"


@pytest.mark.integration
def test_effective_ceremony_mode_default_uses_profile() -> None:
    """When ceremony_mode is default ('full'), effective_ceremony_mode delegates to profile."""
    config = TRWConfig(target_platforms=["claude-code"])
    assert config.ceremony_mode == "full"
    assert config.client_profile.ceremony_mode == "full"
    assert config.effective_ceremony_mode == "full"


@pytest.mark.integration
def test_effective_ceremony_mode_opencode_flat_field_still_full() -> None:
    """The flat ceremony_mode field stays 'full' — only effective_ceremony_mode changes."""
    config = TRWConfig(target_platforms=["opencode"])
    assert config.ceremony_mode == "full"
    assert config.effective_ceremony_mode == "light"


@pytest.mark.unit
def test_write_targets_agents_md_primary_default_false() -> None:
    """WriteTargets.agents_md_primary defaults to False."""
    write_targets = WriteTargets()
    assert write_targets.agents_md_primary is False


@pytest.mark.unit
def test_write_targets_cli_config_default_false() -> None:
    """WriteTargets.cli_config defaults to False."""
    write_targets = WriteTargets()
    assert write_targets.cli_config is False


@pytest.mark.unit
def test_write_targets_agents_md_primary_can_be_set_true() -> None:
    """WriteTargets.agents_md_primary can be set to True."""
    write_targets = WriteTargets(agents_md=True, agents_md_primary=True, instruction_path="AGENTS.md")
    assert write_targets.agents_md_primary is True


@pytest.mark.unit
def test_write_targets_cli_config_can_be_set_true() -> None:
    """WriteTargets.cli_config can be set to True for cursor-cli profiles."""
    write_targets = WriteTargets(cli_config=True)
    assert write_targets.cli_config is True


@pytest.mark.unit
def test_cursor_ide_profile_resolves() -> None:
    """resolve_client_profile('cursor-ide') returns full-ceremony cursor-ide profile."""
    profile = resolve_client_profile("cursor-ide")
    assert profile.client_id == "cursor-ide"
    assert profile.ceremony_mode == "full"
    assert profile.tool_exposure_mode == "all"
    assert profile.write_targets.cursor_rules is True
    assert profile.write_targets.agents_md is True
    assert profile.write_targets.agents_md_primary is False
    assert profile.write_targets.cli_config is False
    assert profile.write_targets.instruction_path == ".cursor/rules/trw-ceremony.mdc"


@pytest.mark.unit
def test_cursor_cli_profile_resolves() -> None:
    """resolve_client_profile('cursor-cli') returns light-ceremony cursor-cli profile."""
    profile = resolve_client_profile("cursor-cli")
    assert profile.client_id == "cursor-cli"
    assert profile.ceremony_mode == "light"
    assert profile.tool_exposure_mode == "standard"
    assert profile.write_targets.agents_md_primary is True
    assert profile.write_targets.cli_config is True
    assert profile.write_targets.instruction_path == "AGENTS.md"
    assert profile.include_framework_ref is False
    assert not hasattr(profile, "include_agent" + "_teams")


@pytest.mark.unit
def test_cursor_id_falls_through_to_unknown_with_rename_hint() -> None:
    """resolve_client_profile('cursor') falls back to claude-code with rename hint logged."""
    with patch("trw_mcp.models.config._profiles.logger") as mock_logger:
        profile = resolve_client_profile("cursor")

    assert profile.client_id == "claude-code"
    mock_logger.warning.assert_called_once()
    call_kwargs = mock_logger.warning.call_args
    message = call_kwargs.kwargs.get("message", "") or str(call_kwargs)
    assert "cursor-ide" in message
    assert "cursor-cli" in message


@pytest.mark.unit
def test_cursor_ide_cli_ceremony_weights_distinct() -> None:
    """cursor-ide and cursor-cli produce distinct ceremony scores for the same event counts."""
    events: list[dict[str, object]] = [
        {"event": "session_start"},
        {"event": "learn_new_entry"},
        {"event": "checkpoint"},
        {"event": "build_check_complete"},
    ]

    ide_profile = resolve_client_profile("cursor-ide")
    cli_profile = resolve_client_profile("cursor-cli")
    ide_score = compute_ceremony_score(events, weights=ide_profile.ceremony_weights)
    cli_score = compute_ceremony_score(events, weights=cli_profile.ceremony_weights)

    assert ide_score != cli_score or ide_profile.ceremony_weights != cli_profile.ceremony_weights
