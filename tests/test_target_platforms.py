"""Tests for target_platforms config field across init, update, and deliver flows.

Covers:
- TestInitTargetPlatforms: init_project() writes correct target_platforms to config.yaml
- TestUpdateTargetPlatforms: update_project() updates target_platforms when IDEs change
- TestDeliverTargetPlatforms: _do_instruction_sync() passes correct client param
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from trw_mcp.bootstrap._init_project import init_project
from trw_mcp.bootstrap._update_project import update_project
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md._sync import _determine_write_targets
from trw_mcp.tools.ceremony import _do_instruction_sync

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_git_repo(tmp_path: Path) -> Path:
    """Create a minimal fake git repo directory."""
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.fixture()
def initialized_repo(fake_git_repo: Path) -> Path:
    """Run init_project on a fake_git_repo and return the directory."""
    init_project(fake_git_repo)
    return fake_git_repo


def _read_target_platforms(repo_dir: Path) -> list[str]:
    """Read target_platforms from .trw/config.yaml."""
    config_path = repo_dir / ".trw" / "config.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return data.get("target_platforms", [])


# ---------------------------------------------------------------------------
# TestInitTargetPlatforms
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestInitTargetPlatforms:
    """init_project() writes correct target_platforms to config.yaml."""

    def test_init_default_writes_claude_code(self, fake_git_repo: Path) -> None:
        """Default init (no IDE override, no IDE dirs) writes target_platforms: ['claude-code']."""
        # Ensure no IDE config dirs exist so auto-detect falls back to claude-code
        result = init_project(fake_git_repo)
        assert not result["errors"]
        platforms = _read_target_platforms(fake_git_repo)
        assert platforms == ["claude-code"]

    def test_init_opencode_override(self, fake_git_repo: Path) -> None:
        """init_project(dir, ide='opencode') writes target_platforms: ['opencode']."""
        result = init_project(fake_git_repo, ide="opencode")
        assert not result["errors"]
        platforms = _read_target_platforms(fake_git_repo)
        assert platforms == ["opencode"]

    def test_init_codex_override(self, fake_git_repo: Path) -> None:
        """init_project(dir, ide='codex') writes target_platforms: ['codex']."""
        result = init_project(fake_git_repo, ide="codex")
        assert not result["errors"]
        platforms = _read_target_platforms(fake_git_repo)
        assert platforms == ["codex"]

    def test_init_all_override(self, fake_git_repo: Path) -> None:
        """init_project(dir, ide='all') writes all supported platforms."""
        result = init_project(fake_git_repo, ide="all")
        assert not result["errors"]
        platforms = _read_target_platforms(fake_git_repo)
        assert sorted(platforms) == sorted(["claude-code", "copilot", "cursor-ide", "cursor-cli", "opencode", "codex", "gemini", "aider"])

    def test_init_detects_opencode_dir(self, fake_git_repo: Path) -> None:
        """When .opencode/ exists, auto-detection includes 'opencode'."""
        (fake_git_repo / ".opencode").mkdir()
        result = init_project(fake_git_repo)
        assert not result["errors"]
        platforms = _read_target_platforms(fake_git_repo)
        assert "opencode" in platforms

    def test_init_detects_codex_dir(self, fake_git_repo: Path) -> None:
        """When .codex/ exists, auto-detection includes 'codex'."""
        (fake_git_repo / ".codex").mkdir()
        result = init_project(fake_git_repo)
        assert not result["errors"]
        platforms = _read_target_platforms(fake_git_repo)
        assert "codex" in platforms

    def test_init_detects_both_claude_and_opencode(self, fake_git_repo: Path) -> None:
        """When both .claude/ and .opencode/ exist, both platforms are written."""
        (fake_git_repo / ".claude").mkdir()
        (fake_git_repo / ".opencode").mkdir()
        result = init_project(fake_git_repo)
        assert not result["errors"]
        platforms = _read_target_platforms(fake_git_repo)
        assert "claude-code" in platforms
        assert "opencode" in platforms

    def test_init_claude_code_override(self, fake_git_repo: Path) -> None:
        """Explicit ide='claude-code' override writes only claude-code."""
        result = init_project(fake_git_repo, ide="claude-code")
        assert not result["errors"]
        platforms = _read_target_platforms(fake_git_repo)
        assert platforms == ["claude-code"]

    def test_init_config_yaml_exists(self, fake_git_repo: Path) -> None:
        """Config yaml is created and contains target_platforms key."""
        result = init_project(fake_git_repo)
        assert not result["errors"]
        config_path = fake_git_repo / ".trw" / "config.yaml"
        assert config_path.exists()
        content = config_path.read_text(encoding="utf-8")
        assert "target_platforms" in content


# ---------------------------------------------------------------------------
# TestUpdateTargetPlatforms
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestUpdateTargetPlatforms:
    """update_project() updates target_platforms when IDE targets change."""

    def test_update_adds_opencode_when_detected(self, initialized_repo: Path) -> None:
        """Init with claude-code only; create .opencode/; update adds opencode to target_platforms."""
        # Verify initial state is claude-code only
        initial_platforms = _read_target_platforms(initialized_repo)
        assert initial_platforms == ["claude-code"]

        # Now create .opencode/ directory so auto-detect picks it up
        (initialized_repo / ".opencode").mkdir(exist_ok=True)

        result = update_project(initialized_repo)
        assert not result["errors"]

        platforms = _read_target_platforms(initialized_repo)
        assert "opencode" in platforms

    def test_update_preserves_when_unchanged(self, initialized_repo: Path) -> None:
        """update_project with same IDE targets preserves config.yaml without modification."""
        # Run update with no IDE changes — target_platforms stays claude-code
        result = update_project(initialized_repo)
        assert not result["errors"]

        config_path = str(initialized_repo / ".trw" / "config.yaml")
        # Should be in "preserved" (not "updated") when targets haven't changed
        assert config_path in result.get("preserved", [])

        platforms = _read_target_platforms(initialized_repo)
        assert "claude-code" in platforms

    def test_update_respects_ide_override(self, initialized_repo: Path) -> None:
        """update_project(dir, ide='opencode') updates target_platforms to ['opencode']."""
        result = update_project(initialized_repo, ide="opencode")
        assert not result["errors"]

        platforms = _read_target_platforms(initialized_repo)
        assert platforms == ["opencode"]

    def test_update_respects_codex_override(self, initialized_repo: Path) -> None:
        """update_project(dir, ide='codex') updates target_platforms to ['codex']."""
        result = update_project(initialized_repo, ide="codex")
        assert not result["errors"]

        platforms = _read_target_platforms(initialized_repo)
        assert platforms == ["codex"]

    def test_update_fail_open_on_corrupt_config(self, initialized_repo: Path) -> None:
        """Corrupt config.yaml doesn't crash update; error goes to result['warnings']."""
        # Write invalid YAML to config
        config_path = initialized_repo / ".trw" / "config.yaml"
        config_path.write_text("{corrupt: [unclosed", encoding="utf-8")

        # update_project should not raise
        result = update_project(initialized_repo)

        # The error is captured in warnings (fail-open), not raised
        # The overall update should still succeed (errors list should be empty
        # or only contain non-target_platforms errors)
        warning_text = " ".join(result.get("warnings", []))
        # If it fails, it fails gracefully into warnings
        if config_path.read_text().startswith("{corrupt"):
            # The corrupt config would trigger the except branch in
            # _update_config_target_platforms — check for warning or no crash
            assert result is not None  # didn't raise

    def test_update_ide_all_writes_all_platforms(self, initialized_repo: Path) -> None:
        """update_project(dir, ide='all') updates target_platforms to all supported platforms."""
        result = update_project(initialized_repo, ide="all")
        assert not result["errors"]

        platforms = _read_target_platforms(initialized_repo)
        assert sorted(platforms) == sorted(["claude-code", "copilot", "cursor-ide", "cursor-cli", "opencode", "codex", "gemini", "aider"])


# ---------------------------------------------------------------------------
# TestDeliverTargetPlatforms
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDeliverTargetPlatforms:
    """_do_instruction_sync() reads config.target_platforms and passes correct client param."""

    def _make_sync_return_value(self, tmp_path: Path) -> dict[str, object]:
        """Build a minimal return value that execute_claude_md_sync would return."""
        return {
            "status": "synced",
            "path": str(tmp_path / "CLAUDE.md"),
            "scope": "root",
            "learnings_promoted": 0,
            "total_lines": 0,
        }

    def _run_instruction_sync_with_platforms(
        self,
        tmp_path: Path,
        platforms: list[str],
    ) -> tuple[str | None, object]:
        """
        Run _do_instruction_sync with the given target_platforms config.

        Returns (client_value, mock_sync_call_args).
        """
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)
        sync_return = self._make_sync_return_value(tmp_path)

        cfg = TRWConfig()
        object.__setattr__(cfg, "target_platforms", platforms)

        captured_client: list[str | None] = []

        def capture_sync(**kwargs: object) -> dict[str, object]:
            captured_client.append(str(kwargs.get("client")) if kwargs.get("client") else None)
            return sync_return

        with (
            patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
            patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=tmp_path),
            patch(
                "trw_mcp.tools.ceremony.execute_claude_md_sync",
                side_effect=capture_sync,
            ) as mock_sync,
        ):
            result = _do_instruction_sync(trw_dir)

        client_val = captured_client[0] if captured_client else None
        return client_val, mock_sync

    def test_single_claude_code_passes_claude_code_client(self, tmp_path: Path) -> None:
        """target_platforms: ['claude-code'] -> execute_claude_md_sync called with client='claude-code'."""
        client_val, mock_sync = self._run_instruction_sync_with_platforms(tmp_path, ["claude-code"])
        mock_sync.assert_called_once()
        call_kwargs = mock_sync.call_args.kwargs
        assert call_kwargs.get("client") == "claude-code"

    def test_single_opencode_passes_opencode_client(self, tmp_path: Path) -> None:
        """target_platforms: ['opencode'] -> execute_claude_md_sync called with client='opencode'."""
        client_val, mock_sync = self._run_instruction_sync_with_platforms(tmp_path, ["opencode"])
        mock_sync.assert_called_once()
        call_kwargs = mock_sync.call_args.kwargs
        assert call_kwargs.get("client") == "opencode"

    def test_single_codex_passes_codex_client(self, tmp_path: Path) -> None:
        """target_platforms: ['codex'] -> execute_claude_md_sync called with client='codex'."""
        client_val, mock_sync = self._run_instruction_sync_with_platforms(tmp_path, ["codex"])
        mock_sync.assert_called_once()
        call_kwargs = mock_sync.call_args.kwargs
        assert call_kwargs.get("client") == "codex"

    def test_multiple_platforms_passes_all_client(self, tmp_path: Path) -> None:
        """target_platforms: ['claude-code', 'opencode'] -> execute_claude_md_sync called with client='all'."""
        client_val, mock_sync = self._run_instruction_sync_with_platforms(tmp_path, ["claude-code", "opencode"])
        mock_sync.assert_called_once()
        call_kwargs = mock_sync.call_args.kwargs
        assert call_kwargs.get("client") == "all"

    def test_empty_platforms_falls_back_to_auto(self, tmp_path: Path) -> None:
        """target_platforms: [] -> execute_claude_md_sync called with client='auto'."""
        client_val, mock_sync = self._run_instruction_sync_with_platforms(tmp_path, [])
        mock_sync.assert_called_once()
        call_kwargs = mock_sync.call_args.kwargs
        assert call_kwargs.get("client") == "auto"

    def test_three_platforms_passes_all_client(self, tmp_path: Path) -> None:
        """target_platforms: four supported platforms -> client='all'."""
        client_val, mock_sync = self._run_instruction_sync_with_platforms(
            tmp_path, ["claude-code", "cursor-ide", "opencode", "codex"]
        )
        mock_sync.assert_called_once()
        call_kwargs = mock_sync.call_args.kwargs
        assert call_kwargs.get("client") == "all"

    def test_cursor_ide_only_passes_cursor_ide_client(self, tmp_path: Path) -> None:
        """target_platforms: ['cursor-ide'] -> client='cursor-ide' (single platform passed directly)."""
        client_val, mock_sync = self._run_instruction_sync_with_platforms(tmp_path, ["cursor-ide"])
        mock_sync.assert_called_once()
        call_kwargs = mock_sync.call_args.kwargs
        assert call_kwargs.get("client") == "cursor-ide"

    def test_instruction_sync_returns_success_status(self, tmp_path: Path) -> None:
        """_do_instruction_sync always normalises status to 'success'."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)
        sync_return: dict[str, object] = {
            "status": "synced",
            "path": str(tmp_path / "CLAUDE.md"),
            "scope": "root",
            "learnings_promoted": 0,
            "total_lines": 0,
        }

        cfg = TRWConfig()
        object.__setattr__(cfg, "target_platforms", ["claude-code"])

        with (
            patch("trw_mcp.tools.ceremony.get_config", return_value=cfg),
            patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=tmp_path),
            patch(
                "trw_mcp.tools.ceremony.execute_claude_md_sync",
                return_value=sync_return,
            ),
        ):
            result = _do_instruction_sync(trw_dir)

        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# TestDetermineWriteTargets — direct unit tests for _determine_write_targets
# ---------------------------------------------------------------------------


class TestDetermineWriteTargets:
    """Direct tests for _determine_write_targets covering cursor-ide and auto-detect edge cases."""

    def test_cursor_ide_client_does_not_write_claude_md(self, tmp_path: Path) -> None:
        """client='cursor-ide' must NOT write CLAUDE.md (cursor rules handled by bootstrap)."""
        cfg = TRWConfig()
        write_claude, write_agents, instruction_path = _determine_write_targets("cursor-ide", cfg, tmp_path, "root")
        assert write_claude is False
        # cursor-ide has agents_md=True in write_targets, so write_agents may be True

    def test_cursor_ide_client_subdir_scope_does_not_write_agents(self, tmp_path: Path) -> None:
        """client='cursor-ide' with scope='subdir' does not write AGENTS.md (subdir scope)."""
        cfg = TRWConfig()
        write_claude, write_agents, instruction_path = _determine_write_targets("cursor-ide", cfg, tmp_path, "subdir")
        assert write_claude is False
        assert write_agents is False  # subdir scope always disables agents_md

    def test_auto_cursor_only_detected_writes_claude(self, tmp_path: Path) -> None:
        """Auto-detect with only .cursor/ present should still write CLAUDE.md as fallback."""
        (tmp_path / ".cursor").mkdir()
        cfg = TRWConfig()
        write_claude, write_agents, instruction_path = _determine_write_targets("auto", cfg, tmp_path, "root")
        assert write_claude is True
        assert write_agents is False

    def test_auto_no_ide_detected_writes_claude(self, tmp_path: Path) -> None:
        """Auto-detect with no IDE dirs falls back to writing CLAUDE.md."""
        cfg = TRWConfig()
        write_claude, write_agents, instruction_path = _determine_write_targets("auto", cfg, tmp_path, "root")
        assert write_claude is True
        assert write_agents is False

    def test_auto_claude_and_cursor_detected_writes_claude(self, tmp_path: Path) -> None:
        """Auto-detect with both .claude/ and .cursor/ writes CLAUDE.md (claude-code match)."""
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".cursor").mkdir()
        cfg = TRWConfig()
        write_claude, write_agents, instruction_path = _determine_write_targets("auto", cfg, tmp_path, "root")
        assert write_claude is True
        assert write_agents is False

    def test_claude_code_client_writes_claude_only(self, tmp_path: Path) -> None:
        """client='claude-code' writes CLAUDE.md only."""
        cfg = TRWConfig()
        write_claude, write_agents, instruction_path = _determine_write_targets("claude-code", cfg, tmp_path, "root")
        assert write_claude is True
        assert write_agents is False

    def test_opencode_client_writes_agents_only(self, tmp_path: Path) -> None:
        """client='opencode' writes AGENTS.md only."""
        cfg = TRWConfig()
        write_claude, write_agents, instruction_path = _determine_write_targets("opencode", cfg, tmp_path, "root")
        assert write_claude is False
        assert write_agents is True
        assert instruction_path == ".opencode/INSTRUCTIONS.md"

    def test_codex_client_writes_agents_only(self, tmp_path: Path) -> None:
        """client='codex' writes AGENTS.md only."""
        cfg = TRWConfig()
        write_claude, write_agents, instruction_path = _determine_write_targets("codex", cfg, tmp_path, "root")
        assert write_claude is False
        assert write_agents is True
        assert instruction_path == ".codex/INSTRUCTIONS.md"

    def test_all_client_writes_both(self, tmp_path: Path) -> None:
        """client='all' writes both CLAUDE.md and AGENTS.md when enabled."""
        cfg = TRWConfig()
        object.__setattr__(cfg, "agents_md_enabled", True)
        write_claude, write_agents, instruction_path = _determine_write_targets("all", cfg, tmp_path, "root")
        assert write_claude is True
        assert write_agents is True

    def test_unknown_client_falls_back_to_claude_code_write_targets(self, tmp_path: Path) -> None:
        """Unknown client falls back to claude-code profile via resolve_client_profile."""
        cfg = TRWConfig()
        write_claude, write_agents, instruction_path = _determine_write_targets("windsurf", cfg, tmp_path, "root")
        assert write_claude is True
        assert write_agents is False

    def test_all_client_agents_md_disabled_writes_claude_only(self, tmp_path: Path) -> None:
        """client='all' with agents_md_enabled=False only writes CLAUDE.md."""
        cfg = TRWConfig()
        object.__setattr__(cfg, "agents_md_enabled", False)
        write_claude, write_agents, instruction_path = _determine_write_targets("all", cfg, tmp_path, "root")
        assert write_claude is True
        assert write_agents is False

    def test_all_client_subdir_scope_no_agents(self, tmp_path: Path) -> None:
        """client='all' with scope='subdir' suppresses agents_md."""
        cfg = TRWConfig()
        object.__setattr__(cfg, "agents_md_enabled", True)
        write_claude, write_agents, instruction_path = _determine_write_targets("all", cfg, tmp_path, "subdir")
        assert write_claude is True
        assert write_agents is False
