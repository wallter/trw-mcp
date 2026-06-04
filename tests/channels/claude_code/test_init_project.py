"""Integration tests for install_claude_code_distill_channels (PRD-DIST-2405 FR41-FR43).

Calls install_claude_code_distill_channels(tmp_path) against a real filesystem
and asserts actual file contents/structure — not just existence.

FR41: All artifacts installed on first call.
FR42: Idempotent — second call produces identical final state (no duplicates).
FR43: Fail-open — partial failures do not raise; result dict contains errors list.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _call_install(target_dir: Path, force: bool = False) -> dict[str, list[str]]:
    from trw_mcp.bootstrap._claude_code_distill_channels import (
        install_claude_code_distill_channels,
    )
    return install_claude_code_distill_channels(target_dir, force=force)


def _load_manifest(target_dir: Path) -> object:
    from trw_mcp.channels._manifest_loader import load

    manifest_path = target_dir / ".trw" / "channels" / "manifest.yaml"
    return load(manifest_path)


# ---------------------------------------------------------------------------
# FR41 — All artifacts installed on first call
# ---------------------------------------------------------------------------


class TestFirstInstall:
    """FR41: All expected artifacts are written on the first call."""

    def test_result_has_expected_keys(self, tmp_path: Path) -> None:
        result = _call_install(tmp_path)
        assert "created" in result
        assert "updated" in result
        assert "preserved" in result
        assert "errors" in result

    def test_cc05_subagent_file_written(self, tmp_path: Path) -> None:
        """CC-05: .claude/agents/trw-distill-explorer.md is created."""
        _call_install(tmp_path)
        agent_file = tmp_path / ".claude" / "agents" / "trw-distill-explorer.md"
        assert agent_file.exists()

    def test_cc05_subagent_content_has_frontmatter(self, tmp_path: Path) -> None:
        """CC-05 subagent content has YAML frontmatter."""
        _call_install(tmp_path)
        agent_file = tmp_path / ".claude" / "agents" / "trw-distill-explorer.md"
        content = agent_file.read_text(encoding="utf-8")
        assert content.startswith("---"), "Subagent file must start with YAML frontmatter"
        assert "trw-distill-explorer" in content

    def test_cc05_subagent_disallows_bash(self, tmp_path: Path) -> None:
        """CC-05 subagent explicitly disallows Bash tool (read-only enforcement)."""
        _call_install(tmp_path)
        content = (tmp_path / ".claude" / "agents" / "trw-distill-explorer.md").read_text(encoding="utf-8")
        assert "Bash" in content
        assert "disallowedTools" in content

    def test_cc03_pretooluse_hook_installed(self, tmp_path: Path) -> None:
        """CC-03: pre-tool-distill-hint.sh is installed to .claude/hooks/."""
        _call_install(tmp_path)
        hook_file = tmp_path / ".claude" / "hooks" / "pre-tool-distill-hint.sh"
        assert hook_file.exists()

    def test_cc03_hook_is_executable(self, tmp_path: Path) -> None:
        """CC-03: installed hook script has execute permission."""
        _call_install(tmp_path)
        hook_file = tmp_path / ".claude" / "hooks" / "pre-tool-distill-hint.sh"
        assert hook_file.stat().st_mode & 0o111, "Hook script must be executable"

    def test_lib_distill_hint_installed(self, tmp_path: Path) -> None:
        """CC-03: lib-distill-hint.sh is installed alongside the main hook."""
        _call_install(tmp_path)
        lib_file = tmp_path / ".claude" / "hooks" / "lib-distill-hint.sh"
        assert lib_file.exists()

    def test_channel_manifest_written(self, tmp_path: Path) -> None:
        """Channel manifest written to .trw/channels/manifest.yaml."""
        _call_install(tmp_path)
        manifest_path = tmp_path / ".trw" / "channels" / "manifest.yaml"
        assert manifest_path.exists()

    def test_channel_manifest_has_five_cc_entries(self, tmp_path: Path) -> None:
        """Five CC channel entries are merged into the manifest."""
        _call_install(tmp_path)
        manifest = _load_manifest(tmp_path)
        cc_ids = {e.id for e in manifest.channels if e.client == "claude-code"}  # type: ignore[attr-defined]
        expected = {
            "cc-01-memory-distill-snapshot",
            "cc-02-claude-md-distill-segment",
            "cc-03-pretooluse-hint",
            "cc-04-posttooluse-correlation",
            "cc-05-distill-explorer",
        }
        assert expected.issubset(cc_ids), f"Missing CC entries: {expected - cc_ids}"

    def test_settings_json_not_written(self, tmp_path: Path) -> None:
        """OQ-01: .claude/settings.json is NOT written (operator opt-in required)."""
        _call_install(tmp_path)
        settings_file = tmp_path / ".claude" / "settings.json"
        assert not settings_file.exists(), (
            "settings.json must NOT be written by install_claude_code_distill_channels — "
            "operator opt-in is required (OQ-01)."
        )

    def test_hook_content_matches_bundled_source(self, tmp_path: Path) -> None:
        """Installed hook content is byte-identical to bundled source."""
        from trw_mcp.bootstrap._claude_code_distill_channels import _get_hook_content

        _call_install(tmp_path)
        bundled = _get_hook_content("pre-tool-distill-hint.sh")
        installed = (
            tmp_path / ".claude" / "hooks" / "pre-tool-distill-hint.sh"
        ).read_text(encoding="utf-8")
        assert installed == bundled

    def test_cc03_hook_has_trap_exit0(self, tmp_path: Path) -> None:
        """Installed hook must contain the FR26 safety contract (trap 'exit 0' EXIT)."""
        _call_install(tmp_path)
        content = (
            tmp_path / ".claude" / "hooks" / "pre-tool-distill-hint.sh"
        ).read_text(encoding="utf-8")
        assert "trap 'exit 0' EXIT" in content, (
            "FR26 safety contract 'trap exit 0 EXIT' missing from hook"
        )

    def test_cc05_entry_in_created_or_preserved(self, tmp_path: Path) -> None:
        """CC-05 result records the subagent in created or preserved list."""
        result = _call_install(tmp_path)
        all_touched = result["created"] + result["preserved"]
        assert any("trw-distill-explorer.md" in p for p in all_touched)


# ---------------------------------------------------------------------------
# FR42 — Idempotency: second call produces same state, no duplicates
# ---------------------------------------------------------------------------


class TestIdempotency:
    """FR42: Running install_claude_code_distill_channels twice is safe and produces no duplicates."""

    def test_second_call_does_not_duplicate_manifest_entries(self, tmp_path: Path) -> None:
        """Second run does not add duplicate CC entries to the manifest."""
        _call_install(tmp_path)
        _call_install(tmp_path)
        manifest = _load_manifest(tmp_path)
        cc_ids = [e.id for e in manifest.channels]
        # No duplicate IDs
        assert len(cc_ids) == len(set(cc_ids)), (
            f"Duplicate manifest entries found: {[i for i in cc_ids if cc_ids.count(i) > 1]}"
        )

    def test_second_call_manifest_has_same_five_cc_entries(self, tmp_path: Path) -> None:
        """After two runs, exactly the same five CC entries exist."""
        _call_install(tmp_path)
        _call_install(tmp_path)
        manifest = _load_manifest(tmp_path)
        cc_ids = {e.id for e in manifest.channels if e.client == "claude-code"}
        expected = {
            "cc-01-memory-distill-snapshot",
            "cc-02-claude-md-distill-segment",
            "cc-03-pretooluse-hint",
            "cc-04-posttooluse-correlation",
            "cc-05-distill-explorer",
        }
        assert expected == cc_ids

    def test_second_call_hook_content_unchanged(self, tmp_path: Path) -> None:
        """Hook file content is identical after two installs."""
        _call_install(tmp_path)
        content1 = (
            tmp_path / ".claude" / "hooks" / "pre-tool-distill-hint.sh"
        ).read_text(encoding="utf-8")
        _call_install(tmp_path)
        content2 = (
            tmp_path / ".claude" / "hooks" / "pre-tool-distill-hint.sh"
        ).read_text(encoding="utf-8")
        assert content1 == content2

    def test_second_call_subagent_content_unchanged(self, tmp_path: Path) -> None:
        """Subagent file content is identical after two installs."""
        _call_install(tmp_path)
        content1 = (
            tmp_path / ".claude" / "agents" / "trw-distill-explorer.md"
        ).read_text(encoding="utf-8")
        _call_install(tmp_path)
        content2 = (
            tmp_path / ".claude" / "agents" / "trw-distill-explorer.md"
        ).read_text(encoding="utf-8")
        assert content1 == content2

    def test_second_call_reports_preserved_not_created(self, tmp_path: Path) -> None:
        """Second call shows hook in preserved (unchanged), not created."""
        _call_install(tmp_path)  # First call: creates
        result2 = _call_install(tmp_path)  # Second call: preserves
        # Hook and subagent should be in preserved (content unchanged)
        all_created2 = result2["created"]
        # The subagent was already written — second run should NOT re-create it
        agent_rel = ".claude/agents/trw-distill-explorer.md"
        assert agent_rel not in all_created2, (
            f"Second install re-created {agent_rel} instead of preserving it"
        )

    def test_idempotent_final_state_same_as_first_run(self, tmp_path: Path) -> None:
        """Final state after 2 runs equals state after 1 run (all files identical)."""
        _call_install(tmp_path)
        # Snapshot state after first run
        agent_content1 = (
            tmp_path / ".claude" / "agents" / "trw-distill-explorer.md"
        ).read_text(encoding="utf-8")
        hook_content1 = (
            tmp_path / ".claude" / "hooks" / "pre-tool-distill-hint.sh"
        ).read_text(encoding="utf-8")
        manifest1 = _load_manifest(tmp_path)
        cc_ids1 = {e.id for e in manifest1.channels}

        _call_install(tmp_path)
        # Compare after second run
        agent_content2 = (
            tmp_path / ".claude" / "agents" / "trw-distill-explorer.md"
        ).read_text(encoding="utf-8")
        hook_content2 = (
            tmp_path / ".claude" / "hooks" / "pre-tool-distill-hint.sh"
        ).read_text(encoding="utf-8")
        manifest2 = _load_manifest(tmp_path)
        cc_ids2 = {e.id for e in manifest2.channels}

        assert agent_content1 == agent_content2
        assert hook_content1 == hook_content2
        assert cc_ids1 == cc_ids2


# ---------------------------------------------------------------------------
# FR43 — Fail-open: partial failures do not raise
# ---------------------------------------------------------------------------


class TestFailOpen:
    """FR43: install_claude_code_distill_channels never raises — errors go to result dict."""

    def test_cc05_subagent_failure_is_captured(self, tmp_path: Path) -> None:
        """CC-05 install failure is captured in errors list, not raised."""
        with patch(
            "trw_mcp.bootstrap._claude_code_distill_channels.install_cc05_subagent",
            side_effect=RuntimeError("subagent failed"),
        ):
            result = _call_install(tmp_path)
        assert any("CC-05" in e for e in result["errors"])

    def test_hook_install_failure_is_captured(self, tmp_path: Path) -> None:
        """CC-03 hook install failure is captured in errors list, not raised."""
        with patch(
            "trw_mcp.bootstrap._claude_code_distill_channels._install_hook",
            side_effect=RuntimeError("hook write failed"),
        ):
            result = _call_install(tmp_path)
        assert any("hook" in e.lower() or "CC-03" in e for e in result["errors"])

    def test_manifest_failure_is_captured(self, tmp_path: Path) -> None:
        """Manifest bootstrap failure is captured in errors list, not raised."""
        with patch(
            "trw_mcp.bootstrap._claude_code_distill_channels.bootstrap_cc_channel_manifest",
            side_effect=RuntimeError("manifest failed"),
        ):
            result = _call_install(tmp_path)
        assert any("manifest" in e.lower() or "CC" in e for e in result["errors"])

    def test_complete_failure_still_returns_dict(self, tmp_path: Path) -> None:
        """Even if everything fails, a result dict is returned (no raise)."""
        with (
            patch(
                "trw_mcp.bootstrap._claude_code_distill_channels.install_cc05_subagent",
                side_effect=RuntimeError("fail1"),
            ),
            patch(
                "trw_mcp.bootstrap._claude_code_distill_channels.bootstrap_cc_channel_manifest",
                side_effect=RuntimeError("fail2"),
            ),
        ):
            result = _call_install(tmp_path)
        assert isinstance(result, dict)
        assert "errors" in result


# ---------------------------------------------------------------------------
# Channel manifest content verification
# ---------------------------------------------------------------------------


class TestManifestContent:
    """Verify channel manifest entry content after install."""

    def test_cc03_entry_has_correct_surface(self, tmp_path: Path) -> None:
        """CC-03 manifest entry has hook_stdout_ephemeral surface."""
        _call_install(tmp_path)
        manifest = _load_manifest(tmp_path)
        cc03 = next(
            (e for e in manifest.channels if e.id == "cc-03-pretooluse-hint"),  # type: ignore[attr-defined]
            None,
        )
        assert cc03 is not None
        # surface may be an enum or a plain string depending on model_validate behavior
        surface_val = cc03.surface  # type: ignore[attr-defined]
        surface_str = surface_val.value if hasattr(surface_val, "value") else str(surface_val)
        assert surface_str == "hook_stdout_ephemeral"

    def test_cc03_entry_has_opt_in_activation_gate(self, tmp_path: Path) -> None:
        """CC-03 manifest entry has activation_gate=cc03_hook_enabled."""
        _call_install(tmp_path)
        manifest = _load_manifest(tmp_path)
        cc03 = next(
            (e for e in manifest.channels if e.id == "cc-03-pretooluse-hint"),  # type: ignore[attr-defined]
            None,
        )
        assert cc03 is not None
        assert cc03.activation_gate == "cc03_hook_enabled"  # type: ignore[attr-defined]

    def test_cc04_entry_has_no_activation_gate(self, tmp_path: Path) -> None:
        """CC-04 is always-on (no activation_gate)."""
        _call_install(tmp_path)
        manifest = _load_manifest(tmp_path)
        cc04 = next(
            (e for e in manifest.channels if e.id == "cc-04-posttooluse-correlation"),  # type: ignore[attr-defined]
            None,
        )
        assert cc04 is not None
        assert not cc04.activation_gate  # type: ignore[attr-defined]

    def test_cc05_entry_has_subagent_file_surface(self, tmp_path: Path) -> None:
        """CC-05 manifest entry has subagent_file surface."""
        _call_install(tmp_path)
        manifest = _load_manifest(tmp_path)
        cc05 = next(
            (e for e in manifest.channels if e.id == "cc-05-distill-explorer"),  # type: ignore[attr-defined]
            None,
        )
        assert cc05 is not None
        surface_val = cc05.surface  # type: ignore[attr-defined]
        surface_str = surface_val.value if hasattr(surface_val, "value") else str(surface_val)
        assert surface_str == "subagent_file"

    def test_manifest_merge_preserves_non_cc_entries(self, tmp_path: Path) -> None:
        """Merging CC entries preserves existing non-claude-code entries."""
        from trw_mcp.channels._manifest_loader import auto_recreate_empty, load, write
        from trw_mcp.channels._manifest_models import ChannelEntry
        from trw_mcp.channels._provenance import now_utc_iso8601

        # Pre-populate manifest with a codex entry
        manifest_path = tmp_path / ".trw" / "channels" / "manifest.yaml"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        auto_recreate_empty(manifest_path)
        existing = load(manifest_path)
        existing.channels.append(
            ChannelEntry(
                id="codex-agents-md-hotspots",
                client="codex",
                surface="codex_agents_md_segment",  # type: ignore[arg-type]
                telemetry_tag="codex_test",
            )
        )
        existing.generated_at = now_utc_iso8601()
        write(existing, manifest_path)

        _call_install(tmp_path)

        merged = _load_manifest(tmp_path)
        ids = {e.id for e in merged.channels}
        assert "codex-agents-md-hotspots" in ids, "Existing codex entry must be preserved"
        assert "cc-01-memory-distill-snapshot" in ids, "New CC entries must be added"


# ---------------------------------------------------------------------------
# _install_hook isolation tests
# ---------------------------------------------------------------------------


class TestInstallHookIsolated:
    """Isolated tests for _install_hook behavior (not via full install_claude_code)."""

    def test_install_hook_creates_file_on_first_run(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._claude_code_distill_channels import _install_hook

        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}
        _install_hook(tmp_path, "pre-tool-distill-hint.sh", result)
        hook_path = tmp_path / ".claude" / "hooks" / "pre-tool-distill-hint.sh"
        assert hook_path.exists()
        assert ".claude/hooks/pre-tool-distill-hint.sh" in result["created"]

    def test_install_hook_preserves_unchanged_content(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._claude_code_distill_channels import _install_hook

        result1: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}
        _install_hook(tmp_path, "pre-tool-distill-hint.sh", result1)

        result2: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}
        _install_hook(tmp_path, "pre-tool-distill-hint.sh", result2)
        assert ".claude/hooks/pre-tool-distill-hint.sh" in result2["preserved"]
        assert len(result2["created"]) == 0

    def test_install_hook_updates_when_content_differs(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._claude_code_distill_channels import _install_hook

        hook_path = tmp_path / ".claude" / "hooks" / "pre-tool-distill-hint.sh"
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text("#!/bin/sh\n# old content\n", encoding="utf-8")

        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}
        _install_hook(tmp_path, "pre-tool-distill-hint.sh", result)
        assert ".claude/hooks/pre-tool-distill-hint.sh" in result["updated"]

    def test_install_hook_absent_source_skips_silently(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._claude_code_distill_channels import _install_hook

        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}
        _install_hook(tmp_path, "nonexistent-hook.sh", result)
        # No error, no file written — silently skipped
        assert len(result["errors"]) == 0
        assert not (tmp_path / ".claude" / "hooks" / "nonexistent-hook.sh").exists()

    def test_install_hook_oserror_captured_in_errors(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._claude_code_distill_channels import _install_hook

        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}
        with patch("pathlib.Path.write_text", side_effect=OSError("permission denied")):
            _install_hook(tmp_path, "pre-tool-distill-hint.sh", result)
        assert len(result["errors"]) > 0
        assert any("pre-tool-distill-hint.sh" in e for e in result["errors"])
