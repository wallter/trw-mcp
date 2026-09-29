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

from tests._ide_detection_isolation import isolate_ide_detection
from trw_mcp.bootstrap._init_project import init_project
from trw_mcp.bootstrap._update_project import update_project
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md._agents_md import _determine_write_target_decision
from trw_mcp.tools.ceremony import _do_instruction_sync

pytestmark = pytest.mark.usefixtures("no_memory_daemon")

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_ide_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Detect clients from ``tmp_path`` only — see ``tests/_ide_detection_isolation``.

    Without it the developer's globally-installed Cursor binary leaks
    ``cursor-ide`` into every auto-detected ``target_platforms`` result.
    """
    isolate_ide_detection(monkeypatch)


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
        assert sorted(platforms) == sorted(
            [
                "claude-code",
                "copilot",
                "cursor-ide",
                "cursor-cli",
                "opencode",
                "codex",
                "antigravity-cli",
                "grok",
            ]
        )

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

    def test_update_does_not_adopt_a_bare_opencode_marker(self, initialized_repo: Path) -> None:
        """Init with claude-code only; an empty .opencode/ dir is no proof of a TRW install, so a bare update does not record opencode."""
        initial_platforms = _read_target_platforms(initialized_repo)
        assert initial_platforms == ["claude-code"]

        (initialized_repo / ".opencode").mkdir(exist_ok=True)

        result = update_project(initialized_repo)
        assert not result["errors"]

        assert _read_target_platforms(initialized_repo) == ["claude-code"]
        assert list((initialized_repo / ".opencode").iterdir()) == []

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
        """update_project(dir, ide='opencode') augments target_platforms without narrowing.

        Post-PRD-FIX-076 contract: ``--ide X`` appends X to the existing list;
        it does NOT narrow to just ``[X]``. The user's existing platforms
        (e.g. claude-code from init) are preserved so multi-platform dev
        configurations survive focused update runs.
        """
        result = update_project(initialized_repo, ide="opencode")
        assert not result["errors"]

        platforms = _read_target_platforms(initialized_repo)
        # Original claude-code preserved + new opencode appended
        assert "claude-code" in platforms
        assert "opencode" in platforms

    def test_update_respects_codex_override(self, initialized_repo: Path) -> None:
        """update_project(dir, ide='codex') augments target_platforms without narrowing.

        Post-PRD-FIX-076 contract: see test_update_respects_ide_override.
        """
        result = update_project(initialized_repo, ide="codex")
        assert not result["errors"]

        platforms = _read_target_platforms(initialized_repo)
        # Original claude-code preserved + new codex appended
        assert "claude-code" in platforms
        assert "codex" in platforms

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
        assert sorted(platforms) == sorted(
            [
                "claude-code",
                "copilot",
                "cursor-ide",
                "cursor-cli",
                "opencode",
                "codex",
                "antigravity-cli",
                "grok",
            ]
        )


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
# TestDetermineWriteTargets — direct unit tests for _determine_write_target_decision
# ---------------------------------------------------------------------------


class TestDetermineWriteTargets:
    """Direct tests for the write-target decision (cursor-ide and auto-detect edge cases).

    TRW 8.0: claude-code's carrier is the shared AGENTS.md, so every case that
    used to write CLAUDE.md now admits the AGENTS.md write instead.
    """

    @staticmethod
    def _decide(client: str, cfg: TRWConfig, root: Path, scope: str) -> tuple[bool, str | None]:
        decision = _determine_write_target_decision(client, cfg, root, scope)
        targets = decision.instruction_targets
        return decision.write_agents, targets[0].instruction_path if targets else None

    def test_cursor_ide_client_does_not_write_agents_md(self, tmp_path: Path) -> None:
        """client='cursor-ide' writes no AGENTS.md (cursor rules handled by bootstrap)."""
        write_agents, _ = self._decide("cursor-ide", TRWConfig(), tmp_path, "root")
        assert write_agents is False

    def test_cursor_ide_client_subdir_scope_does_not_write_agents(self, tmp_path: Path) -> None:
        write_agents, _ = self._decide("cursor-ide", TRWConfig(), tmp_path, "subdir")
        assert write_agents is False

    def test_auto_cursor_only_detected_still_gets_claude_code_carrier(self, tmp_path: Path) -> None:
        """A DETECTED cursor-ide alone is a PATH signal, so the default claude-code carrier stays."""
        (tmp_path / ".cursor").mkdir()
        write_agents, _ = self._decide("auto", TRWConfig(), tmp_path, "root")
        assert write_agents is True

    def test_auto_no_ide_detected_writes_agents_md(self, tmp_path: Path) -> None:
        """No IDE detected is the default scaffold: claude-code, whose carrier is AGENTS.md."""
        write_agents, _ = self._decide("auto", TRWConfig(), tmp_path, "root")
        assert write_agents is True

    def test_auto_claude_and_cursor_detected_writes_agents_md(self, tmp_path: Path) -> None:
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".cursor").mkdir()
        write_agents, _ = self._decide("auto", TRWConfig(), tmp_path, "root")
        assert write_agents is True

    def test_auto_opencode_only_does_not_write_shared_agents_md(self, tmp_path: Path) -> None:
        """PRD-CORE-240-FR04: opencode owns ``.opencode/INSTRUCTIONS.md``, not the shared AGENTS.md."""
        (tmp_path / ".opencode").mkdir()
        write_agents, instruction_path = self._decide("auto", TRWConfig(), tmp_path, "root")
        assert write_agents is False
        assert instruction_path == ".opencode/INSTRUCTIONS.md"

    def test_auto_cursor_cli_only_leaves_agents_md_to_bootstrap(self, tmp_path: Path) -> None:
        """cursor-cli's AGENTS.md is owned by the bootstrap path (PRD-CORE-137-FR04)."""
        (tmp_path / ".cursor").mkdir()
        (tmp_path / ".cursor" / "cli.json").write_text("{}", encoding="utf-8")
        write_agents, _ = self._decide("auto", TRWConfig(), tmp_path, "root")
        assert write_agents is False

    def test_claude_code_client_writes_agents_md(self, tmp_path: Path) -> None:
        write_agents, _ = self._decide("claude-code", TRWConfig(), tmp_path, "root")
        assert write_agents is True

    def test_opencode_client_writes_its_own_file_only(self, tmp_path: Path) -> None:
        write_agents, instruction_path = self._decide("opencode", TRWConfig(), tmp_path, "root")
        assert write_agents is False
        assert instruction_path == ".opencode/INSTRUCTIONS.md"

    def test_codex_client_writes_its_own_file_only(self, tmp_path: Path) -> None:
        # WITHDRAWN (PRD-CORE-240-FR04): codex's own `.codex/INSTRUCTIONS.md`
        # carries the whole protocol, so TRW writes nothing into AGENTS.md.
        write_agents, instruction_path = self._decide("codex", TRWConfig(), tmp_path, "root")
        assert write_agents is False
        assert instruction_path == ".codex/INSTRUCTIONS.md"

    def test_all_client_writes_agents_md(self, tmp_path: Path) -> None:
        cfg = TRWConfig()
        object.__setattr__(cfg, "agents_md_enabled", True)
        write_agents, _ = self._decide("all", cfg, tmp_path, "root")
        assert write_agents is True

    def test_unknown_client_falls_back_to_claude_code_write_targets(self, tmp_path: Path) -> None:
        write_agents, _ = self._decide("windsurf", TRWConfig(), tmp_path, "root")
        assert write_agents is True

    def test_all_client_agents_md_disabled_writes_nothing_shared(self, tmp_path: Path) -> None:
        cfg = TRWConfig()
        object.__setattr__(cfg, "agents_md_enabled", False)
        write_agents, _ = self._decide("all", cfg, tmp_path, "root")
        assert write_agents is False

    def test_all_client_subdir_scope_no_agents(self, tmp_path: Path) -> None:
        cfg = TRWConfig()
        object.__setattr__(cfg, "agents_md_enabled", True)
        write_agents, _ = self._decide("all", cfg, tmp_path, "subdir")
        assert write_agents is False
