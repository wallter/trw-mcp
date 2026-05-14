"""Split bootstrap update core behavior tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.models.config import TRWConfig


class TestUpdateProjectBasics:
    """Test update_project basic behavior."""

    def test_requires_trw_installed(self, tmp_path: Path) -> None:
        """update_project errors if .trw/ does not exist."""
        result = update_project(tmp_path)
        assert len(result["errors"]) == 1
        assert ".trw/ not found" in result["errors"][0]

    def test_no_errors_on_initialized_repo(self, initialized_repo: Path) -> None:
        """update_project succeeds on an initialized repo."""
        result = update_project(initialized_repo)
        assert not result["errors"]

    def test_reports_updated_files(self, initialized_repo: Path) -> None:
        """update_project reports framework files as updated."""
        result = update_project(initialized_repo)
        assert len(result["updated"]) > 0
        # Should have updated hooks, skills, agents, framework files
        updated_str = "\n".join(result["updated"])
        assert "FRAMEWORK.md" in updated_str
        assert "hooks" in updated_str

    def test_reports_preserved_files(self, initialized_repo: Path) -> None:
        """update_project reports user files as preserved."""
        result = update_project(initialized_repo)
        preserved_str = "\n".join(result["preserved"])
        assert "config.yaml" in preserved_str


@pytest.mark.unit
class TestUpdatePreservesUserFiles:
    """Test that update_project never overwrites user-customized files."""

    def test_preserves_config_yaml(self, initialized_repo: Path) -> None:
        """User's config.yaml is never overwritten."""
        config_path = initialized_repo / ".trw" / "config.yaml"
        config_path.write_text("custom_setting: true\n", encoding="utf-8")

        update_project(initialized_repo)

        content = config_path.read_text(encoding="utf-8")
        assert "custom_setting: true" in content

    def test_preserves_learnings(self, initialized_repo: Path) -> None:
        """User's learnings index is never overwritten."""
        index_path = initialized_repo / ".trw" / "learnings" / "index.yaml"
        index_path.write_text("entries:\n- id: L001\n", encoding="utf-8")

        update_project(initialized_repo)

        content = index_path.read_text(encoding="utf-8")
        assert "L001" in content

    def test_preserves_mcp_json(self, initialized_repo: Path) -> None:
        """User's .mcp.json is never overwritten."""
        mcp_path = initialized_repo / ".mcp.json"
        mcp_path.write_text('{"custom": true}\n', encoding="utf-8")

        update_project(initialized_repo)

        content = mcp_path.read_text(encoding="utf-8")
        assert '"custom": true' in content


@pytest.mark.unit
class TestUpdateOverwritesFrameworkFiles:
    """Test that update_project overwrites framework-managed files."""

    def test_updates_framework_md(self, initialized_repo: Path) -> None:
        """FRAMEWORK.md is overwritten with latest version."""
        fw_path = initialized_repo / ".trw" / "frameworks" / "FRAMEWORK.md"
        fw_path.write_text("old framework content", encoding="utf-8")

        update_project(initialized_repo)

        content = fw_path.read_text(encoding="utf-8")
        assert content != "old framework content"
        assert TRWConfig().framework_version in content

    def test_updates_hooks(self, initialized_repo: Path) -> None:
        """Hook scripts are overwritten with latest versions."""
        hook_path = initialized_repo / ".claude" / "hooks" / "session-start.sh"
        hook_path.write_text("old hook", encoding="utf-8")

        update_project(initialized_repo)

        content = hook_path.read_text(encoding="utf-8")
        assert content != "old hook"

    def test_updates_skills(self, initialized_repo: Path) -> None:
        """Skill files are overwritten with latest versions."""
        skill_path = initialized_repo / ".claude" / "skills" / "trw-deliver" / "SKILL.md"
        skill_path.write_text("old skill", encoding="utf-8")

        update_project(initialized_repo)

        content = skill_path.read_text(encoding="utf-8")
        assert content != "old skill"

    def test_updates_agents(self, initialized_repo: Path) -> None:
        """Agent files are overwritten with latest versions."""
        agent_path = initialized_repo / ".claude" / "agents" / "trw-implementer.md"
        agent_path.write_text("old agent", encoding="utf-8")

        update_project(initialized_repo)

        content = agent_path.read_text(encoding="utf-8")
        assert content != "old agent"


@pytest.mark.unit
class TestUpdateClaudeMdSmartMerge:
    """Test that update_project smart-merges CLAUDE.md."""

    def test_preserves_user_sections(self, initialized_repo: Path) -> None:
        """User content above TRW markers is preserved."""
        claude_md = initialized_repo / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")

        # Add user content before the TRW section
        user_section = "## My Custom Section\n\nThis is user content.\n\n"
        content = content.replace("<!-- TRW AUTO-GENERATED", user_section + "<!-- TRW AUTO-GENERATED")
        claude_md.write_text(content, encoding="utf-8")

        update_project(initialized_repo)

        updated = claude_md.read_text(encoding="utf-8")
        assert "My Custom Section" in updated
        assert "This is user content." in updated
        assert "trw_session_start" in updated  # TRW section still present

    def test_updates_trw_section(self, initialized_repo: Path) -> None:
        """TRW auto-generated section is updated."""
        claude_md = initialized_repo / "CLAUDE.md"

        update_project(initialized_repo)

        content = claude_md.read_text(encoding="utf-8")
        assert "<!-- trw:start -->" in content
        assert "<!-- trw:end -->" in content
        assert "trw_session_start" in content

    def test_appends_trw_section_if_missing(self, initialized_repo: Path) -> None:
        """If CLAUDE.md has no TRW markers, append the section."""
        claude_md = initialized_repo / "CLAUDE.md"
        claude_md.write_text("# My Project\n\nNo TRW section here.\n", encoding="utf-8")

        update_project(initialized_repo)

        content = claude_md.read_text(encoding="utf-8")
        assert "# My Project" in content
        assert "<!-- trw:start -->" in content
        assert "trw_session_start" in content

    def test_creates_claude_md_if_missing(self, initialized_repo: Path) -> None:
        """If CLAUDE.md doesn't exist, create it from template."""
        claude_md = initialized_repo / "CLAUDE.md"
        claude_md.unlink()

        result = update_project(initialized_repo)
        assert not result["errors"]
        assert claude_md.exists()
        assert "trw_session_start" in claude_md.read_text(encoding="utf-8")

class TestUpdateCreatesNewArtifacts:
    """Test that update_project creates new artifacts from newer versions."""

    def test_creates_new_skill(self, initialized_repo: Path) -> None:
        """New skills in bundled data are deployed."""
        # All skills should exist after update
        result = update_project(initialized_repo)
        assert not result["errors"]

        skills_dir = initialized_repo / ".claude" / "skills"
        deployed = sorted(d.name for d in skills_dir.iterdir() if d.is_dir())
        # Should have all expected skills
        assert "trw-deliver" in deployed
        assert "trw-learn" in deployed
        assert "trw-project-health" in deployed

    def test_creates_new_agent(self, initialized_repo: Path) -> None:
        """New agents in bundled data are deployed."""
        result = update_project(initialized_repo)
        assert not result["errors"]

        agents_dir = initialized_repo / ".claude" / "agents"
        deployed = sorted(f.name for f in agents_dir.iterdir() if f.suffix == ".md")
        assert "trw-implementer.md" in deployed
        assert "trw-auditor.md" in deployed


@pytest.mark.unit
class TestUpdateWarningsAndVersionCheck:
    """Test update_project warnings, version check, and restart guidance."""

    def test_includes_restart_warning(self, initialized_repo: Path) -> None:
        """update_project always warns about restarting sessions."""
        result = update_project(initialized_repo)
        assert "warnings" in result
        assert any("Restart" in w for w in result["warnings"])

    def test_includes_version_check(self, initialized_repo: Path) -> None:
        """update_project checks installed package version."""
        result = update_project(initialized_repo)
        # Should have either a version match (preserved) or mismatch (warning)
        version_related = [p for p in result["preserved"] if "trw-mcp package" in p] + [
            w for w in result["warnings"] if "trw-mcp" in w and "differs" in w
        ]
        assert len(version_related) > 0

    def test_warnings_key_always_present(self, initialized_repo: Path) -> None:
        """update_project result always includes 'warnings' key."""
        result = update_project(initialized_repo)
        assert "warnings" in result
        assert isinstance(result["warnings"], list)

class TestRootFrameworkMd:
    """Test that init/update deploy FRAMEWORK.md to the project root."""

    def test_init_creates_root_framework_md(self, fake_git_repo: Path) -> None:
        """init_project creates FRAMEWORK.md at the project root."""
        result = init_project(fake_git_repo)
        assert not result["errors"]

        root_fw = fake_git_repo / "FRAMEWORK.md"
        assert root_fw.is_file()
        content = root_fw.read_text(encoding="utf-8")
        assert TRWConfig().framework_version in content

    def test_init_root_matches_cached(self, fake_git_repo: Path) -> None:
        """Root FRAMEWORK.md matches .trw/frameworks/FRAMEWORK.md after init."""
        init_project(fake_git_repo)

        root_fw = fake_git_repo / "FRAMEWORK.md"
        cached_fw = fake_git_repo / ".trw" / "frameworks" / "FRAMEWORK.md"
        assert root_fw.read_text(encoding="utf-8") == cached_fw.read_text(encoding="utf-8")

    def test_update_overwrites_stale_root_framework_md(self, initialized_repo: Path) -> None:
        """update_project overwrites a stale root FRAMEWORK.md."""
        root_fw = initialized_repo / "FRAMEWORK.md"
        root_fw.write_text("old stale content v16.0", encoding="utf-8")

        result = update_project(initialized_repo)
        assert not result["errors"]

        content = root_fw.read_text(encoding="utf-8")
        assert content != "old stale content v16.0"
        assert TRWConfig().framework_version in content

    def test_update_root_matches_cached(self, initialized_repo: Path) -> None:
        """After update, root FRAMEWORK.md matches cached version."""
        update_project(initialized_repo)

        root_fw = initialized_repo / "FRAMEWORK.md"
        cached_fw = initialized_repo / ".trw" / "frameworks" / "FRAMEWORK.md"
        assert root_fw.read_text(encoding="utf-8") == cached_fw.read_text(encoding="utf-8")
