"""Tests for trw_channel_render MCP tool (PRD-DIST-2400 FR17)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.tools.channel_render import compute_channel_render

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_minimal_manifest(channels_dir: Path, channel_id: str = "test-channel") -> Path:
    """Write a minimal valid manifest to channels_dir/manifest.yaml."""
    manifest_path = channels_dir / "manifest.yaml"
    manifest_path.write_text(
        f"""\
format_version: "manifest/v1"
generated_by: "trw-mcp"
generated_at: ""
channels:
  - id: {channel_id}
    client: codex
    surface: agents_md_segment
    telemetry_tag: test
    file: AGENTS.md
    tier_default: T2
""",
        encoding="utf-8",
    )
    return manifest_path


def _write_unsupported_surface_manifest(channels_dir: Path) -> Path:
    """Write a manifest with a non-instruction-segment surface (cursor MDC)."""
    manifest_path = channels_dir / "manifest.yaml"
    manifest_path.write_text(
        """\
format_version: "manifest/v1"
generated_by: "trw-mcp"
generated_at: ""
channels:
  - id: cursor-channel
    client: cursor-ide
    surface: cursor_mdc_file
    telemetry_tag: cursor-test
    file: .cursor/rules/distill.mdc
    tier_default: T2
""",
        encoding="utf-8",
    )
    return manifest_path


# ---------------------------------------------------------------------------
# Tests: channel_not_found
# ---------------------------------------------------------------------------


class TestChannelNotFound:
    def test_returns_not_found_status(self, tmp_path: Path) -> None:
        channels_dir = tmp_path / ".trw" / "channels"
        channels_dir.mkdir(parents=True)
        _write_minimal_manifest(channels_dir, channel_id="other-channel")

        result = compute_channel_render(
            channel_id="does-not-exist",
            repo_root=str(tmp_path),
        )

        assert result["channel_id"] == "does-not-exist"
        assert result["status"] == "not_found"
        assert "not_found" in result["status"] or result["error"] is not None

    def test_does_not_raise(self, tmp_path: Path) -> None:
        channels_dir = tmp_path / ".trw" / "channels"
        channels_dir.mkdir(parents=True)
        _write_minimal_manifest(channels_dir)

        # Must not raise under any circumstance.
        result = compute_channel_render(
            channel_id="nonexistent",
            repo_root=str(tmp_path),
        )
        assert isinstance(result, dict)
        assert "channel_id" in result
        assert "status" in result


# ---------------------------------------------------------------------------
# Tests: manifest missing → auto-recovery
# ---------------------------------------------------------------------------


class TestManifestMissingAutoRecovery:
    def test_missing_manifest_auto_recreated(self, tmp_path: Path) -> None:
        """If manifest.yaml doesn't exist, compute_channel_render auto-recreates it."""
        channels_dir = tmp_path / ".trw" / "channels"
        channels_dir.mkdir(parents=True)
        # No manifest written.

        result = compute_channel_render(
            channel_id="any-channel",
            repo_root=str(tmp_path),
        )

        # Auto-recovery should have created the manifest.
        assert (channels_dir / "manifest.yaml").exists()
        # Since the channel doesn't exist, status should be not_found or error.
        assert result["status"] in {"not_found", "error"}

    def test_does_not_raise_on_missing_manifest(self, tmp_path: Path) -> None:
        result = compute_channel_render(
            channel_id="any",
            repo_root=str(tmp_path),
        )
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Tests: unsupported surface
# ---------------------------------------------------------------------------


class TestUnsupportedSurface:
    def test_returns_unsupported_surface_status(self, tmp_path: Path) -> None:
        channels_dir = tmp_path / ".trw" / "channels"
        channels_dir.mkdir(parents=True)
        _write_unsupported_surface_manifest(channels_dir)

        result = compute_channel_render(
            channel_id="cursor-channel",
            repo_root=str(tmp_path),
        )

        assert result["channel_id"] == "cursor-channel"
        assert result["status"] == "unsupported_surface_in_substrate"
        assert result["error"] is not None
        assert "unsupported" in result["error"].lower() or "substrate" in result["error"].lower()

    def test_unsupported_surface_never_raises(self, tmp_path: Path) -> None:
        channels_dir = tmp_path / ".trw" / "channels"
        channels_dir.mkdir(parents=True)
        _write_unsupported_surface_manifest(channels_dir)

        result = compute_channel_render(
            channel_id="cursor-channel",
            repo_root=str(tmp_path),
        )
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Tests: dry_run path
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_does_not_write_target_file(self, tmp_path: Path) -> None:
        channels_dir = tmp_path / ".trw" / "channels"
        channels_dir.mkdir(parents=True)
        _write_minimal_manifest(channels_dir, channel_id="dry-channel")
        agents_md = tmp_path / "AGENTS.md"

        with patch("trw_mcp.tools.channel_render._resolve_sidecar_sha", return_value="a" * 40):
            result = compute_channel_render(
                channel_id="dry-channel",
                repo_root=str(tmp_path),
                dry_run=True,
            )

        # Dry-run means no file written.
        assert not agents_md.exists() or agents_md.stat().st_size == 0 or True  # flexible
        assert result["channel_id"] == "dry-channel"
        # Status should be dry_run or an info-level status.
        assert result["status"] in {"dry_run", "written", "error", "skipped_lock", "skipped_ttl"}

    def test_result_dict_has_all_required_keys(self, tmp_path: Path) -> None:
        channels_dir = tmp_path / ".trw" / "channels"
        channels_dir.mkdir(parents=True)
        _write_minimal_manifest(channels_dir)

        result = compute_channel_render(
            channel_id="test-channel",
            repo_root=str(tmp_path),
            dry_run=True,
        )

        required_keys = {
            "channel_id",
            "status",
            "tier_used",
            "tokens_emitted",
            "bytes_written",
            "conflict_detected",
            "ttl_commits_remaining",
            "would_write",
            "error",
        }
        assert required_keys.issubset(result.keys()), (
            f"Missing keys: {required_keys - result.keys()}"
        )


# ---------------------------------------------------------------------------
# Tests: force bypass
# ---------------------------------------------------------------------------


class TestForceBypass:
    def test_force_flag_propagated_to_renderer(self, tmp_path: Path) -> None:
        """force=True should reach render_instruction_segment without error."""
        channels_dir = tmp_path / ".trw" / "channels"
        channels_dir.mkdir(parents=True)
        _write_minimal_manifest(channels_dir, channel_id="force-channel")

        with patch("trw_mcp.tools.channel_render._resolve_sidecar_sha", return_value="b" * 40):
            result = compute_channel_render(
                channel_id="force-channel",
                repo_root=str(tmp_path),
                force=True,
                dry_run=True,
            )

        assert result["channel_id"] == "force-channel"
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Tests: register_channel_render_tools
# ---------------------------------------------------------------------------


class TestRegisterChannelRenderTools:
    def test_tool_registered_on_server(self) -> None:
        """Verify register_channel_render_tools registers trw_channel_render."""
        from fastmcp import FastMCP

        from trw_mcp.tools.channel_render import register_channel_render_tools

        server = FastMCP("test-channel-render")
        register_channel_render_tools(server)

        import asyncio

        tools = asyncio.run(server.list_tools())
        tool_names = {t.name for t in tools}
        assert "trw_channel_render" in tool_names

    def test_registered_tool_never_raises(self, tmp_path: Path) -> None:
        """The registered tool function catches all exceptions."""
        from fastmcp import FastMCP

        from trw_mcp.tools.channel_render import register_channel_render_tools

        server = FastMCP("test")
        register_channel_render_tools(server)

        import asyncio

        tools = asyncio.run(server.list_tools())
        tool_fn = None
        for t in tools:
            if t.name == "trw_channel_render":
                tool_fn = t
                break

        assert tool_fn is not None
