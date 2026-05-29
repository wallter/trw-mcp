"""Split bootstrap merge/metadata tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project
from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401


class TestMcpJsonMerge:
    """Test that .mcp.json merge preserves user servers and ensures trw entry."""

    def test_merge_preserves_user_servers(self, initialized_repo: Path) -> None:
        """Existing user-configured MCP servers survive update."""
        mcp_path = initialized_repo / ".mcp.json"
        mcp_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "trw": {"command": "trw-mcp", "args": ["--debug"]},
                        "my-tool": {"command": "my-tool-server", "args": []},
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        update_project(initialized_repo)

        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        assert "my-tool" in data["mcpServers"]
        assert data["mcpServers"]["my-tool"]["command"] == "my-tool-server"

    def test_merge_restores_trw_key(self, initialized_repo: Path) -> None:
        """Missing 'trw' key is added back during update."""
        mcp_path = initialized_repo / ".mcp.json"
        mcp_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "my-tool": {"command": "my-tool-server", "args": []},
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        result = update_project(initialized_repo)

        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        assert "trw" in data["mcpServers"]
        assert "command" in data["mcpServers"]["trw"]
        # Should be reported as an update
        assert any("trw entry" in u for u in result["updated"])

    def test_merge_updates_trw_command(self, initialized_repo: Path) -> None:
        """Stale trw command path is refreshed."""
        mcp_path = initialized_repo / ".mcp.json"
        mcp_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "trw": {"command": "/old/path/trw-mcp", "args": []},
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        update_project(initialized_repo)

        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        assert data["mcpServers"]["trw"]["command"] != "/old/path/trw-mcp"

    def test_merge_creates_if_missing(self, fake_git_repo: Path) -> None:
        """Fresh .mcp.json generated when init runs on a new project."""
        result = init_project(fake_git_repo)
        assert not result["errors"]

        mcp_path = fake_git_repo / ".mcp.json"
        assert mcp_path.exists()
        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        assert "trw" in data["mcpServers"]


# ── Installer Metadata Tests ──────────────────────────────────────────────


@pytest.mark.unit
class TestInstallerMetadata:
    """Test .trw/installer-meta.yaml creation and updates."""

    def test_metadata_written_on_init(self, fake_git_repo: Path) -> None:
        """installer-meta.yaml created during init_project."""
        init_project(fake_git_repo)
        meta_path = fake_git_repo / ".trw" / "installer-meta.yaml"
        assert meta_path.exists()

        from trw_mcp.state.persistence import FileStateReader

        reader = FileStateReader()
        data = reader.read_yaml(meta_path)
        assert data["installed_by"] == "trw-mcp init-project"
        assert "framework_version" in data
        assert "package_version" in data
        assert data["hooks_count"] > 0

    def test_metadata_updated_on_update(self, initialized_repo: Path) -> None:
        """installer-meta.yaml refreshed during update_project."""
        update_project(initialized_repo)
        meta_path = initialized_repo / ".trw" / "installer-meta.yaml"
        assert meta_path.exists()

        from trw_mcp.state.persistence import FileStateReader

        reader = FileStateReader()
        data = reader.read_yaml(meta_path)
        assert data["installed_by"] == "trw-mcp update-project"
        assert data["skills_count"] > 0
        assert data["agents_count"] > 0


# ── Dry-Run Tests ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestDryRun:
    """Test --dry-run mode doesn't modify files."""

    def test_dry_run_no_file_changes(self, initialized_repo: Path) -> None:
        """--dry-run doesn't modify any files."""
        framework_md = initialized_repo / ".trw" / "frameworks" / "FRAMEWORK.md"

        # Modify a file to create a diff
        framework_md.write_text("old content", encoding="utf-8")
        old_content = framework_md.read_text(encoding="utf-8")

        result = update_project(initialized_repo, dry_run=True)
        assert not result["errors"]

        # File should not be changed
        assert framework_md.read_text(encoding="utf-8") == old_content

    def test_dry_run_reports_would_update(self, initialized_repo: Path) -> None:
        """Dry run result lists expected changes."""
        # Modify a framework file to create a diff
        framework_md = initialized_repo / ".trw" / "frameworks" / "FRAMEWORK.md"
        framework_md.write_text("old content", encoding="utf-8")

        result = update_project(initialized_repo, dry_run=True)

        # Should report would-update items
        assert any("would" in u for u in result["updated"])
        # Should include dry-run warning
        assert any("DRY RUN" in w for w in result["warnings"])

    def test_dry_run_reports_missing_trw_entry(self, initialized_repo: Path) -> None:
        """Dry run detects missing trw key in .mcp.json."""
        mcp_path = initialized_repo / ".mcp.json"
        mcp_path.write_text(
            json.dumps(
                {
                    "mcpServers": {"other": {"command": "x", "args": []}},
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        result = update_project(initialized_repo, dry_run=True)

        assert any("trw entry" in u for u in result["updated"])

    def test_dry_run_no_installer_metadata(self, initialized_repo: Path) -> None:
        """Dry run doesn't write installer metadata."""
        meta_path = initialized_repo / ".trw" / "installer-meta.yaml"
        meta_existed = meta_path.exists()

        update_project(initialized_repo, dry_run=True)

        if not meta_existed:
            assert not meta_path.exists()


# ── Default Config Tests ──────────────────────────────────────────────────


@pytest.mark.unit
class TestDefaultConfig:
    """Test _default_config() matches TRWConfig defaults."""

    def test_default_config_matches_trwconfig(self) -> None:
        """_default_config() claude_md_max_lines matches TRWConfig default."""
        from trw_mcp.bootstrap import _default_config
        from trw_mcp.models.config import TRWConfig

        config_text = _default_config()
        default_model = TRWConfig()
        assert f"claude_md_max_lines: {default_model.claude_md_max_lines}" in config_text

    def test_default_config_includes_runs_root(self) -> None:
        """_default_config() includes runs_root with the default value."""
        from trw_mcp.bootstrap import _default_config

        config_text = _default_config()
        assert "runs_root: .trw/runs" in config_text

    def test_default_config_custom_runs_root(self) -> None:
        """_default_config(runs_root=...) emits the custom value."""
        from trw_mcp.bootstrap import _default_config

        config_text = _default_config(runs_root="docs/runs")
        assert "runs_root: docs/runs" in config_text
        assert ".trw/runs" not in config_text


# ── Verification Tests ───────────────────────────────────────────────────


@pytest.mark.unit
class TestVerifyInstallation:
    """Test post-update health verification."""

    def test_verify_passes_healthy(self, initialized_repo: Path) -> None:
        """Verification passes on a clean install — no health warnings."""
        result = update_project(initialized_repo)
        # Filter to only health-check warnings (not restart/version warnings)
        health_warnings = [
            w for w in result["warnings"] if "executable" in w or "missing" in w.lower() or "not valid" in w
        ]
        assert len(health_warnings) == 0, f"Unexpected health warnings: {health_warnings}"


# ── Manifest Tests ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestManagedArtifactsManifest:
    """Test .trw/managed-artifacts.yaml creation and stale-cleanup behavior."""

    def test_manifest_written_on_init(self, fake_git_repo: Path) -> None:
        """init_project writes the managed-artifacts manifest."""
        init_project(fake_git_repo)
        manifest_path = fake_git_repo / ".trw" / "managed-artifacts.yaml"
        assert manifest_path.exists()

        from trw_mcp.state.persistence import FileStateReader

        data = FileStateReader().read_yaml(manifest_path)
        assert isinstance(data, dict)
        assert data["version"] == 2
        skills = data.get("skills", [])
        assert isinstance(skills, list)
        assert "trw-deliver" in skills
        assert "trw-learn" in skills

    def test_manifest_written_on_update(self, initialized_repo: Path) -> None:
        """update_project refreshes the managed-artifacts manifest."""
        update_project(initialized_repo)
        manifest_path = initialized_repo / ".trw" / "managed-artifacts.yaml"
        assert manifest_path.exists()

        from trw_mcp.state.persistence import FileStateReader

        data = FileStateReader().read_yaml(manifest_path)
        assert isinstance(data, dict)
        agents = data.get("agents", [])
        assert isinstance(agents, list)
        assert "trw-auditor.md" in agents
        assert "trw-implementer.md" in agents

    def test_manifest_lists_all_bundled_artifacts(self, fake_git_repo: Path) -> None:
        """Manifest includes all bundled skills, agents, and hooks."""
        init_project(fake_git_repo)
        manifest_path = fake_git_repo / ".trw" / "managed-artifacts.yaml"

        from trw_mcp.state.persistence import FileStateReader

        data = FileStateReader().read_yaml(manifest_path)
        assert isinstance(data, dict)
        skills = data.get("skills", [])
        agents = data.get("agents", [])
        hooks = data.get("hooks", [])
        assert isinstance(skills, list)
        assert isinstance(agents, list)
        assert isinstance(hooks, list)

        # These asserts are for TRW bundled SKILLS & AGENTS, if these numbers are being changed,
        # ensure the change is for a skill/agent that should be released and distributed with the TRW Framework
        # or if the skill/agent/change is for an internal monorepo skill
        assert len(skills) == 26
        assert len(agents) == 12
        assert len(hooks) > 0
