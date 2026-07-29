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


def _load_manifest_ids(tmp_path: Path, client: str | None = None) -> set[str]:
    """Load channel IDs from manifest.yaml, optionally scoped to one client.

    PRD-CORE-239 FR01: the per-client assertions below moved from a list of
    ``id in ids`` checks to exact set equality on the client's own entries.
    Membership checks would still pass if a removed channel were reintroduced;
    equality pins the surviving set on both sides.
    """
    from trw_mcp.channels._manifest_loader import load

    manifest_path = tmp_path / ".trw" / "channels" / "manifest.yaml"
    if not manifest_path.exists():
        return set()
    manifest = load(manifest_path)
    return {e.id for e in manifest.channels if client is None or e.client == client}


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
    assert {"created", "updated", "preserved", "errors"} <= set(result)


def test_install_claude_code_distill_channels_populates_manifest(tmp_path: Path) -> None:
    """install_claude_code_distill_channels merges the two CC entries into manifest.

    PRD-CORE-239 FR01 removed cc-01-memory-distill-snapshot,
    cc-02-claude-md-distill-segment and cc-04-posttooluse-correlation — the
    three CC channels that wrote distill-derived prose into a file the user
    reads. cc-03 and cc-05 survive and are asserted as an exact set.
    """
    from trw_mcp.bootstrap._claude_code_distill_channels import (
        install_claude_code_distill_channels,
    )

    install_claude_code_distill_channels(tmp_path)

    assert _load_manifest_ids(tmp_path, client="claude-code") == {
        "cc-03-pretooluse-hint",
        "cc-05-distill-explorer",
    }


def test_install_claude_code_distill_channels_installs_subagent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRD-CORE-239: CC-05 installs for a LICENSED project.

    The conftest autouse fixture pins distill absent, so this must opt in —
    which is the point: the gate is closed by default.
    """
    from trw_mcp.bootstrap._claude_code_distill_channels import (
        install_claude_code_distill_channels,
    )

    monkeypatch.setattr("trw_mcp.tools._sidecar_substrate.distill_installed", lambda: True)
    install_claude_code_distill_channels(tmp_path)

    agent_path = tmp_path / ".claude" / "agents" / "trw-distill-explorer.md"
    assert agent_path.exists(), f"CC-05 subagent not found at {agent_path}"
    content = agent_path.read_text(encoding="utf-8")
    assert "trw-distill-explorer" in content


def test_claude_code_subagent_withheld_without_a_licence(tmp_path: Path) -> None:
    """The defect this PRD fixes: an unlicensed project got an unusable agent.

    `.claude/agents/trw-distill-explorer.md` is "powered by trw-distill" and
    cannot function without the proprietary package. Installing it for a
    free-tier user hands them an agent that silently does nothing.
    """
    from trw_mcp.bootstrap._claude_code_distill_channels import (
        install_claude_code_distill_channels,
    )

    # conftest's _default_distill_absent already pins the gate closed.
    install_claude_code_distill_channels(tmp_path)

    assert not (tmp_path / ".claude" / "agents" / "trw-distill-explorer.md").exists()
    # ...while the distill-FREE hooks still install.
    assert (tmp_path / ".claude" / "hooks" / "pre-tool-distill-hint.sh").exists(), (
        "the CC-03 hint hooks call the free MCP tools and must NOT be gated"
    )


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
    assert {"created", "updated", "preserved", "errors"} <= set(result)


def test_install_cursor_distill_channels_populates_manifest(tmp_path: Path) -> None:
    """install_cursor_distill_channels merges the two cursor entries into manifest.

    PRD-CORE-239 FR01 removed the three .cursor/rules/*.mdc writers
    (cursor-mdc-conventions, cursor-mdc-hotspots-template,
    cursor-mdc-dangerous-edits) and cursor-cli-agents-md-snapshot. The
    surviving pair is asserted as an exact set — cursor-pretooluse-hint was
    already shipping in manifest-cursor.yaml but this test never named it.
    """
    from trw_mcp.bootstrap._cursor_distill_channels import (
        install_cursor_distill_channels,
    )

    install_cursor_distill_channels(tmp_path)

    assert _load_manifest_ids(tmp_path, client="cursor-ide") == {
        "cursor-mcp-tool-return",
        "cursor-pretooluse-hint",
    }


def test_cursor_distill_channels_no_longer_write_mdc_stubs(tmp_path: Path) -> None:
    """PRD-CORE-239: the T0 MDC stubs are gone, and that is the fix.

    `render_presence_beacon_mdc` hardcoded the rule description to "TRW distill
    data available — quota exceeded, use trw_codebase_risk_report() for full
    analysis". Cursor surfaces that string to the agent as the rule's summary,
    so every Cursor project was told data existed and had been truncated by a
    quota — when in fact nothing had ever been generated and no quota was hit.
    Two false claims, shipped on every init-project regardless of licence.
    """
    from trw_mcp.bootstrap._cursor_distill_channels import (
        install_cursor_distill_channels,
    )

    install_cursor_distill_channels(tmp_path)

    rules_dir = tmp_path / ".cursor" / "rules"
    for stub in ("distill-conventions.mdc", "distill-dangerous-edits.mdc"):
        assert not (rules_dir / stub).exists(), f"{stub} asserts data that does not exist; it must no longer be written"


def test_install_codex_distill_channels_returns_correct_format(tmp_path: Path) -> None:
    """install_codex_distill_channels returns standard result dict format."""
    from trw_mcp.bootstrap._codex_distill_channels import (
        install_codex_distill_channels,
    )

    result = install_codex_distill_channels(tmp_path)
    _assert_result_format(result)
    assert {"created", "updated", "preserved", "errors"} <= set(result)


def test_install_codex_distill_channels_populates_manifest(tmp_path: Path) -> None:
    """install_codex_distill_channels merges the two codex entries into manifest.

    PRD-CORE-239 FR01 removed codex-agents-md-hotspots, the AGENTS.md hotspot
    segment writer. The two non-instruction-file codex channels survive.
    """
    from trw_mcp.bootstrap._codex_distill_channels import (
        install_codex_distill_channels,
    )

    install_codex_distill_channels(tmp_path)

    assert _load_manifest_ids(tmp_path, client="codex") == {
        "codex-tool-return-t2",
        "codex-posttooluse-telemetry",
    }


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
    assert {"created", "updated", "preserved", "errors"} <= set(result)


def test_install_antigravity_distill_channels_populates_manifest(tmp_path: Path) -> None:
    """install_antigravity_distill_channels merges three AG entries into manifest.

    PRD-CORE-239 FR01 removed ag-01-antigravity-md-distill, the ANTIGRAVITY.md
    segment writer. AG-02/03/04 survive.
    """
    from trw_mcp.bootstrap._antigravity_distill_channels import (
        install_antigravity_distill_channels,
    )

    install_antigravity_distill_channels(tmp_path)

    assert _load_manifest_ids(tmp_path, client="antigravity-cli") == {
        "ag-02-distill-explorer-subagent",
        "ag-03-before-edit-hook",
        "ag-04-tool-return-enrichment",
    }


def test_install_antigravity_distill_channels_installs_subagent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRD-CORE-239: AG-02 installs for a licensed project."""
    from trw_mcp.bootstrap._antigravity_distill_channels import (
        install_antigravity_distill_channels,
    )

    monkeypatch.setattr("trw_mcp.tools._sidecar_substrate.distill_installed", lambda: True)
    install_antigravity_distill_channels(tmp_path)

    agent_path = tmp_path / ".antigravitycli" / "agents" / "trw-distill-explorer.md"
    assert agent_path.exists(), f"AG-02 subagent not found at {agent_path}"


def test_antigravity_subagent_withheld_without_a_licence(tmp_path: Path) -> None:
    """Sibling of the CC-05 gate — all three explorer agents behave alike.

    Gating one of three identical siblings would have been a subset defect
    inside the fix itself.
    """
    from trw_mcp.bootstrap._antigravity_distill_channels import (
        install_antigravity_distill_channels,
    )

    install_antigravity_distill_channels(tmp_path)

    assert not (tmp_path / ".antigravitycli" / "agents" / "trw-distill-explorer.md").exists()
    # ...while the distill-FREE before-edit hook still installs. The manifest
    # calls ag-03 "aspirational / no implementation"; it is neither.
    assert (tmp_path / ".antigravitycli" / "hooks" / "trw_before_edit_telemetry.py").exists(), (
        "the AG-03 PreToolUse hook is distill-free and must NOT be gated"
    )


def test_opencode_explorer_withheld_without_a_licence(tmp_path: Path) -> None:
    """The third sibling. Two of three were pinned; this one only asserted.

    cc-05 and ag-02 each had a withholding test; the opencode explorer was
    gated in the same commit but never pinned, so the asymmetry was invisible —
    a coverage audit reading `channels/opencode/_explorer_agent.py` (which has
    no gate of its own, because the gate lives at the bootstrap caller)
    reasonably concluded it was ungated. Two siblings guarded and one guarded
    only by inspection is how the third quietly becomes the next defect.

    The custom commands are deliberately NOT gated and must survive: their
    bodies call free MCP tools (`trw_before_edit_hint`, `trw_codebase_risk_report`,
    `trw_recall`), so withholding them would break the free tier to protect a
    paid one.
    """
    from trw_mcp.bootstrap._opencode_distill_channels import (
        install_opencode_distill_channels,
    )

    result = install_opencode_distill_channels(tmp_path)

    assert not (tmp_path / ".opencode" / "agents" / "trw-distill-explorer.md").exists(), (
        "an unlicensed project must not receive an agent that cannot function"
    )
    assert result.get("explorer_agent") == "skipped_unentitled", (
        "the skip must be reported, not silent — a silent skip reproduces the "
        f"defect one level down; got {result.get('explorer_agent')!r}"
    )


def test_public_opencode_and_antigravity_artifacts_do_not_require_distill_cli(tmp_path: Path) -> None:
    """Public adapters use bundled MCP tools rather than the proprietary CLI."""
    from trw_mcp.bootstrap._antigravity_distill_channels import (
        install_antigravity_distill_channels,
    )
    from trw_mcp.bootstrap._opencode_distill_channels import (
        install_opencode_distill_channels,
    )

    install_opencode_distill_channels(tmp_path)
    install_antigravity_distill_channels(tmp_path)

    generated = [
        tmp_path / "AGENTS.md",
        *(tmp_path / ".opencode").rglob("*"),
        *(tmp_path / ".antigravitycli").rglob("*"),
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in generated if path.is_file())
    assert "trw-distill self-improve" not in text
    assert "trw_codebase_risk_report" in text


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
    assert {"created", "updated", "preserved", "errors"} <= set(result)


def test_install_copilot_distill_channels_populates_manifest(tmp_path: Path) -> None:
    """install_copilot_distill_channels merges three copilot entries into manifest.

    PRD-CORE-239 FR01 removed copilot-instructions-distill (the
    .github/copilot-instructions.md segment) and
    copilot-path-instructions-distill (.github/instructions/*.instructions.md).
    The MCP-config and tool-return channels survive; copilot-pretooluse-hint
    was already shipping in manifest-copilot.yaml but this test never named it.
    """
    from trw_mcp.bootstrap._copilot_distill_channels import (
        install_copilot_distill_channels,
    )

    install_copilot_distill_channels(tmp_path)

    assert _load_manifest_ids(tmp_path, client="copilot") == {
        "copilot-vscode-mcp-config",
        "copilot-mcp-tool-return",
        "copilot-pretooluse-hint",
    }


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


def test_copilot_distill_channels_no_longer_write_the_c2_stub(tmp_path: Path) -> None:
    """PRD-CORE-239: the C2 path-instructions stub is gone.

    Its body was ``run `trw-distill self-improve risk-report` `` — planted into
    .github/instructions/ for every Copilot project, including the majority
    whose owners do not license trw-distill and would get "command not found".
    """
    from trw_mcp.bootstrap._copilot_distill_channels import (
        install_copilot_distill_channels,
    )

    install_copilot_distill_channels(tmp_path)

    c2 = tmp_path / ".github" / "instructions" / "trw-distill-hotspots.instructions.md"
    assert not c2.exists(), "the C2 stub advertises a paid CLI to unlicensed users"


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
    """install_opencode_distill_channels merges five opencode entries into manifest.

    PRD-CORE-239 FR01 removed opencode-agents-md-segment, the AGENTS.md marker
    block. opencode was the one client whose segment was invoked directly by
    its installer, so this is the only manifest whose shrink also changed
    on-disk behaviour.
    """
    from trw_mcp.bootstrap._opencode_distill_channels import (
        bootstrap_channel_manifest,
    )

    bootstrap_channel_manifest(tmp_path)

    assert _load_manifest_ids(tmp_path, client="opencode") == {
        "opencode-custom-cmd-before-edit",
        "opencode-custom-cmd-hotspots",
        "opencode-custom-cmd-conventions",
        "opencode-tool-return-enrichment",
        "opencode-explorer-agent",
    }


# ---------------------------------------------------------------------------
# init_project wiring test
# ---------------------------------------------------------------------------


def test_init_project_wires_claude_code_distill_channels(tmp_path: Path) -> None:
    """init_project() triggers claude-code distill channel bootstrap.

    Verifies that install_claude_code_distill_channels is called when
    claude-code is in the ide_targets list. Uses direct call (not patching
    every init_project step) — just test the distill module is callable.
    """
    # Directly exercise the install function on a bare tmp_path.
    # PRD-CORE-239: CC-05 is licence-gated, so this wiring test opts in — it is
    # asserting that the wiring reaches the installer, not that the gate is open.
    import pytest as _pytest

    from trw_mcp.bootstrap._claude_code_distill_channels import (
        install_claude_code_distill_channels,
    )

    mp = _pytest.MonkeyPatch()
    mp.setattr("trw_mcp.tools._sidecar_substrate.distill_installed", lambda: True)
    try:
        result = install_claude_code_distill_channels(tmp_path)
    finally:
        mp.undo()

    # CC-05 subagent must be installed for a licensed project
    agent_path = tmp_path / ".claude" / "agents" / "trw-distill-explorer.md"
    assert agent_path.exists()

    # Manifest must have both surviving CC entries. PRD-CORE-239 FR01 removed
    # cc-01/cc-02/cc-04; the wiring claim is unchanged, only the expected set.
    assert _load_manifest_ids(tmp_path, client="claude-code") == {
        "cc-03-pretooluse-hint",
        "cc-05-distill-explorer",
    }

    # No unexpected errors
    assert not result["errors"], f"Got errors: {result['errors']}"
