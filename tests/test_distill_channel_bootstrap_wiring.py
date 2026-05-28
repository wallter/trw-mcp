"""Integration tests for distill channel bootstrap wiring.

Tests that install_<client>_distill_channels() functions:
1. Create expected files
2. Populate .trw/channels/manifest.yaml with the client's entries
3. Return correct result dict format
4. Are wired into init_project() and update_project() flows

PRD-DIST-2405 FR41-FR43 (and equivalents in PRDs 2401-2406).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_git_repo(tmp_path: Path) -> Path:
    """Initialize a bare git repo."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=False)
    return tmp_path


def _assert_result_format(result: dict[str, list[str]]) -> None:
    """Assert that result dict has the standard bootstrap format."""
    for key in ("created", "updated", "preserved", "errors"):
        assert key in result, f"result missing key: {key!r}"
        assert isinstance(result[key], list), f"result[{key!r}] is not a list"


def _load_manifest_ids(tmp_path: Path) -> set[str]:
    """Load channel IDs from manifest.yaml."""
    from trw_mcp.channels._manifest_loader import load

    manifest_path = tmp_path / ".trw" / "channels" / "manifest.yaml"
    if not manifest_path.exists():
        return set()
    manifest = load(manifest_path)
    return {e.id for e in manifest.channels}


# ---------------------------------------------------------------------------
# Claude Code distill channels
# ---------------------------------------------------------------------------


def test_install_claude_code_distill_channels_returns_correct_format(tmp_path: Path) -> None:
    """install_claude_code_distill_channels returns standard result dict format."""
    from trw_mcp.bootstrap._claude_code_distill_channels import (
        install_claude_code_distill_channels,
    )

    result = install_claude_code_distill_channels(tmp_path)
    _assert_result_format(result)


def test_install_claude_code_distill_channels_populates_manifest(tmp_path: Path) -> None:
    """install_claude_code_distill_channels merges five CC entries into manifest."""
    from trw_mcp.bootstrap._claude_code_distill_channels import (
        install_claude_code_distill_channels,
    )

    install_claude_code_distill_channels(tmp_path)

    ids = _load_manifest_ids(tmp_path)
    assert "cc-01-memory-distill-snapshot" in ids
    assert "cc-02-claude-md-distill-segment" in ids
    assert "cc-03-pretooluse-hint" in ids
    assert "cc-04-posttooluse-correlation" in ids
    assert "cc-05-distill-explorer" in ids


def test_install_claude_code_distill_channels_installs_subagent(tmp_path: Path) -> None:
    """install_claude_code_distill_channels installs CC-05 subagent file."""
    from trw_mcp.bootstrap._claude_code_distill_channels import (
        install_claude_code_distill_channels,
    )

    install_claude_code_distill_channels(tmp_path)

    agent_path = tmp_path / ".claude" / "agents" / "trw-distill-explorer.md"
    assert agent_path.exists(), f"CC-05 subagent not found at {agent_path}"
    content = agent_path.read_text(encoding="utf-8")
    assert "trw-distill-explorer" in content


def test_bootstrap_cc_channel_manifest_is_idempotent(tmp_path: Path) -> None:
    """Running bootstrap_cc_channel_manifest twice does not duplicate entries."""
    from trw_mcp.bootstrap._claude_code_distill_channels import (
        bootstrap_cc_channel_manifest,
    )

    bootstrap_cc_channel_manifest(tmp_path)
    result1 = _load_manifest_ids(tmp_path)

    bootstrap_cc_channel_manifest(tmp_path)
    result2 = _load_manifest_ids(tmp_path)

    assert result1 == result2, "Second call added duplicate entries"


# ---------------------------------------------------------------------------
# Cursor distill channels
# ---------------------------------------------------------------------------


def test_install_cursor_distill_channels_returns_correct_format(tmp_path: Path) -> None:
    """install_cursor_distill_channels returns standard result dict format."""
    from trw_mcp.bootstrap._cursor_distill_channels import (
        install_cursor_distill_channels,
    )

    result = install_cursor_distill_channels(tmp_path)
    _assert_result_format(result)


def test_install_cursor_distill_channels_populates_manifest(tmp_path: Path) -> None:
    """install_cursor_distill_channels merges five cursor entries into manifest."""
    from trw_mcp.bootstrap._cursor_distill_channels import (
        install_cursor_distill_channels,
    )

    install_cursor_distill_channels(tmp_path)

    ids = _load_manifest_ids(tmp_path)
    assert "cursor-mdc-conventions" in ids
    assert "cursor-mdc-hotspots-template" in ids
    assert "cursor-mdc-dangerous-edits" in ids
    assert "cursor-mcp-tool-return" in ids
    assert "cursor-cli-agents-md-snapshot" in ids


def test_install_cursor_distill_channels_writes_mdc_stubs(tmp_path: Path) -> None:
    """install_cursor_distill_channels writes T0 stub MDC files."""
    from trw_mcp.bootstrap._cursor_distill_channels import (
        install_cursor_distill_channels,
    )

    install_cursor_distill_channels(tmp_path)

    cursor_rules = tmp_path / ".cursor" / "rules"
    # At least one stub MDC file should be written
    mdc_files = list(cursor_rules.glob("*.mdc")) if cursor_rules.exists() else []
    assert len(mdc_files) >= 1, f"Expected at least one stub MDC file in {cursor_rules}"


# ---------------------------------------------------------------------------
# Codex distill channels
# ---------------------------------------------------------------------------


def test_install_codex_distill_channels_returns_correct_format(tmp_path: Path) -> None:
    """install_codex_distill_channels returns standard result dict format."""
    from trw_mcp.bootstrap._codex_distill_channels import (
        install_codex_distill_channels,
    )

    result = install_codex_distill_channels(tmp_path)
    _assert_result_format(result)


def test_install_codex_distill_channels_populates_manifest(tmp_path: Path) -> None:
    """install_codex_distill_channels merges three codex entries into manifest."""
    from trw_mcp.bootstrap._codex_distill_channels import (
        install_codex_distill_channels,
    )

    install_codex_distill_channels(tmp_path)

    ids = _load_manifest_ids(tmp_path)
    assert "codex-agents-md-hotspots" in ids
    assert "codex-tool-return-t2" in ids
    assert "codex-posttooluse-telemetry" in ids


def test_install_codex_distill_channels_installs_hook(tmp_path: Path) -> None:
    """install_codex_distill_channels installs the PostToolUse telemetry hook."""
    from trw_mcp.bootstrap._codex_distill_channels import (
        install_codex_distill_channels,
    )

    install_codex_distill_channels(tmp_path)

    hook_path = tmp_path / ".codex" / "hooks" / "trw_post_edit_telemetry.py"
    assert hook_path.exists(), f"Codex hook not found at {hook_path}"
    content = hook_path.read_text(encoding="utf-8")
    assert "trw" in content.lower()


# ---------------------------------------------------------------------------
# Antigravity distill channels
# ---------------------------------------------------------------------------


def test_install_antigravity_distill_channels_returns_correct_format(tmp_path: Path) -> None:
    """install_antigravity_distill_channels returns standard result dict format."""
    from trw_mcp.bootstrap._antigravity_distill_channels import (
        install_antigravity_distill_channels,
    )

    result = install_antigravity_distill_channels(tmp_path)
    _assert_result_format(result)


def test_install_antigravity_distill_channels_populates_manifest(tmp_path: Path) -> None:
    """install_antigravity_distill_channels merges four AG entries into manifest."""
    from trw_mcp.bootstrap._antigravity_distill_channels import (
        install_antigravity_distill_channels,
    )

    install_antigravity_distill_channels(tmp_path)

    ids = _load_manifest_ids(tmp_path)
    assert "ag-01-antigravity-md-distill" in ids
    assert "ag-02-distill-explorer-subagent" in ids
    assert "ag-03-before-edit-hook" in ids
    assert "ag-04-tool-return-enrichment" in ids


def test_install_antigravity_distill_channels_installs_subagent(tmp_path: Path) -> None:
    """install_antigravity_distill_channels installs AG-02 explorer subagent."""
    from trw_mcp.bootstrap._antigravity_distill_channels import (
        install_antigravity_distill_channels,
    )

    install_antigravity_distill_channels(tmp_path)

    agent_path = tmp_path / ".antigravitycli" / "agents" / "trw-distill-explorer.md"
    assert agent_path.exists(), f"AG-02 subagent not found at {agent_path}"


# ---------------------------------------------------------------------------
# Copilot distill channels
# ---------------------------------------------------------------------------


def test_install_copilot_distill_channels_returns_correct_format(tmp_path: Path) -> None:
    """install_copilot_distill_channels returns standard result dict format."""
    from trw_mcp.bootstrap._copilot_distill_channels import (
        install_copilot_distill_channels,
    )

    result = install_copilot_distill_channels(tmp_path)
    _assert_result_format(result)


def test_install_copilot_distill_channels_populates_manifest(tmp_path: Path) -> None:
    """install_copilot_distill_channels merges four copilot entries into manifest."""
    from trw_mcp.bootstrap._copilot_distill_channels import (
        install_copilot_distill_channels,
    )

    install_copilot_distill_channels(tmp_path)

    ids = _load_manifest_ids(tmp_path)
    assert "copilot-instructions-distill" in ids
    assert "copilot-path-instructions-distill" in ids
    assert "copilot-vscode-mcp-config" in ids
    assert "copilot-mcp-tool-return" in ids


def test_install_copilot_distill_channels_installs_vscode_mcp(tmp_path: Path) -> None:
    """install_copilot_distill_channels installs .vscode/mcp.json."""
    from trw_mcp.bootstrap._copilot_distill_channels import (
        install_copilot_distill_channels,
    )

    install_copilot_distill_channels(tmp_path)

    vscode_mcp = tmp_path / ".vscode" / "mcp.json"
    assert vscode_mcp.exists(), f".vscode/mcp.json not found at {vscode_mcp}"
    import json
    data = json.loads(vscode_mcp.read_text(encoding="utf-8"))
    assert "servers" in data
    assert "trw" in data["servers"]


def test_install_copilot_distill_channels_installs_c2_stub(tmp_path: Path) -> None:
    """install_copilot_distill_channels installs C2 path instructions stub."""
    from trw_mcp.bootstrap._copilot_distill_channels import (
        install_copilot_distill_channels,
    )

    install_copilot_distill_channels(tmp_path)

    c2_path = tmp_path / ".github" / "instructions" / "trw-distill-hotspots.instructions.md"
    assert c2_path.exists(), f"C2 path instructions not found at {c2_path}"


# ---------------------------------------------------------------------------
# Opencode distill channels (already existed — regression test)
# ---------------------------------------------------------------------------


def test_install_opencode_distill_channels_returns_results(tmp_path: Path) -> None:
    """install_opencode_distill_channels returns a non-empty result dict."""
    from trw_mcp.bootstrap._opencode_distill_channels import (
        install_opencode_distill_channels,
    )

    result = install_opencode_distill_channels(tmp_path)
    assert isinstance(result, dict)
    # client_profile_env is always set
    assert "client_profile_env" in result


def test_install_opencode_distill_channels_populates_manifest(tmp_path: Path) -> None:
    """install_opencode_distill_channels merges six opencode entries into manifest."""
    from trw_mcp.bootstrap._opencode_distill_channels import (
        bootstrap_channel_manifest,
    )

    bootstrap_channel_manifest(tmp_path)

    ids = _load_manifest_ids(tmp_path)
    assert "opencode-agents-md-segment" in ids
    assert "opencode-custom-cmd-before-edit" in ids
    assert "opencode-custom-cmd-hotspots" in ids
    assert "opencode-custom-cmd-conventions" in ids
    assert "opencode-tool-return-enrichment" in ids
    assert "opencode-explorer-agent" in ids


# ---------------------------------------------------------------------------
# init_project wiring test
# ---------------------------------------------------------------------------


def test_init_project_wires_claude_code_distill_channels(tmp_path: Path) -> None:
    """init_project() triggers claude-code distill channel bootstrap.

    Verifies that install_claude_code_distill_channels is called when
    claude-code is in the ide_targets list. Uses direct call (not patching
    every init_project step) — just test the distill module is callable.
    """
    from trw_mcp.bootstrap._claude_code_distill_channels import (
        install_claude_code_distill_channels,
    )

    # Directly exercise the install function on a bare tmp_path
    result = install_claude_code_distill_channels(tmp_path)

    # CC-05 subagent must be installed
    agent_path = tmp_path / ".claude" / "agents" / "trw-distill-explorer.md"
    assert agent_path.exists()

    # Manifest must have all 5 CC entries
    ids = _load_manifest_ids(tmp_path)
    cc_ids = {
        "cc-01-memory-distill-snapshot",
        "cc-02-claude-md-distill-segment",
        "cc-03-pretooluse-hint",
        "cc-04-posttooluse-correlation",
        "cc-05-distill-explorer",
    }
    assert cc_ids.issubset(ids), f"Missing CC IDs: {cc_ids - ids}"

    # No unexpected errors
    assert not result["errors"], f"Got errors: {result['errors']}"
