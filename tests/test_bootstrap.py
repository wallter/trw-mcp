"""Tests for trw_mcp.bootstrap — PRD-INFRA-006."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project


@pytest.fixture()
def fake_git_repo(tmp_path: Path) -> Path:
    """Create a minimal fake git repo directory."""
    (tmp_path / ".git").mkdir()
    return tmp_path


# ── Structure Tests ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestInitProjectStructure:
    """Test that init_project creates all expected directories and files."""

    def test_creates_trw_directories(self, fake_git_repo: Path) -> None:
        result = init_project(fake_git_repo)
        assert not result["errors"]

        expected_dirs = [
            ".trw/frameworks",
            ".trw/context",
            ".trw/templates",
            ".trw/learnings/entries",
            ".trw/scripts",
            ".claude/hooks",
        ]
        for d in expected_dirs:
            assert (fake_git_repo / d).is_dir(), f"Missing directory: {d}"

    def test_creates_framework_files(self, fake_git_repo: Path) -> None:
        result = init_project(fake_git_repo)
        assert not result["errors"]

        expected_files = [
            ".trw/frameworks/FRAMEWORK.md",
            ".trw/context/behavioral_protocol.yaml",
            ".trw/templates/claude_md.md",
            ".trw/config.yaml",
            ".trw/learnings/index.yaml",
            ".trw/.gitignore",
            ".claude/settings.json",
            ".mcp.json",
            "CLAUDE.md",
        ]
        for f in expected_files:
            assert (fake_git_repo / f).is_file(), f"Missing file: {f}"

    def test_creates_all_expected_files(self, fake_git_repo: Path) -> None:
        """All files reported as created on first run."""
        result = init_project(fake_git_repo)
        assert not result["errors"]
        assert len(result["created"]) > 0
        assert len(result["skipped"]) == 0


# ── Idempotency Tests ───────────────────────────────────────────────────


@pytest.mark.unit
class TestIdempotency:
    """Test that re-running without --force skips existing files."""

    def test_second_run_skips_existing(self, fake_git_repo: Path) -> None:
        init_project(fake_git_repo)
        result2 = init_project(fake_git_repo)

        assert not result2["errors"]
        # All files should be skipped, no new creates (dirs already exist too)
        assert len(result2["skipped"]) > 0
        # Dirs don't report as created when they already exist
        file_creates = [c for c in result2["created"] if not c.endswith("/")]
        assert len(file_creates) == 0

    def test_force_overwrites(self, fake_git_repo: Path) -> None:
        init_project(fake_git_repo)

        # Modify a file
        claude_md = fake_git_repo / "CLAUDE.md"
        claude_md.write_text("modified content", encoding="utf-8")

        result2 = init_project(fake_git_repo, force=True)
        assert not result2["errors"]

        # File should be re-created
        restored = claude_md.read_text(encoding="utf-8")
        assert restored != "modified content"
        assert "trw_session_start" in restored


# ── Validation Tests ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidation:
    """Test error handling and validation."""

    def test_not_git_repo_errors(self, tmp_path: Path) -> None:
        """Should error when target is not a git repository."""
        result = init_project(tmp_path)
        assert len(result["errors"]) == 1
        assert ".git/ not found" in result["errors"][0]
        assert len(result["created"]) == 0


# ── Content Tests ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestContent:
    """Test content correctness of generated files."""

    def test_mcp_json_valid(self, fake_git_repo: Path) -> None:
        init_project(fake_git_repo)
        mcp_json = fake_git_repo / ".mcp.json"
        data = json.loads(mcp_json.read_text(encoding="utf-8"))

        assert "mcpServers" in data
        assert "trw" in data["mcpServers"]
        assert "command" in data["mcpServers"]["trw"]
        assert "args" in data["mcpServers"]["trw"]

    def test_claude_md_has_protocol(self, fake_git_repo: Path) -> None:
        init_project(fake_git_repo)
        content = (fake_git_repo / "CLAUDE.md").read_text(encoding="utf-8")

        assert "trw_session_start" in content
        assert "trw_deliver" in content

    def test_framework_md_is_v24(self, fake_git_repo: Path) -> None:
        init_project(fake_git_repo)
        path = fake_git_repo / ".trw" / "frameworks" / "FRAMEWORK.md"
        content = path.read_text(encoding="utf-8")
        assert "v24.0_TRW" in content

    def test_config_yaml_has_defaults(self, fake_git_repo: Path) -> None:
        init_project(fake_git_repo)
        content = (fake_git_repo / ".trw" / "config.yaml").read_text(encoding="utf-8")
        assert "task_root: docs" in content
        assert "debug: false" in content

    def test_learnings_index_initialized(self, fake_git_repo: Path) -> None:
        init_project(fake_git_repo)
        path = fake_git_repo / ".trw" / "learnings" / "index.yaml"
        content = path.read_text(encoding="utf-8")
        assert "entries: []" in content

    def test_gitignore_has_expected_patterns(self, fake_git_repo: Path) -> None:
        init_project(fake_git_repo)
        content = (fake_git_repo / ".trw" / ".gitignore").read_text(encoding="utf-8")
        assert "context/" in content
        assert "logs/" in content
        assert "reflections/" in content
        assert "*.jsonl" in content

    def test_behavioral_protocol_copied(self, fake_git_repo: Path) -> None:
        init_project(fake_git_repo)
        path = fake_git_repo / ".trw" / "context" / "behavioral_protocol.yaml"
        content = path.read_text(encoding="utf-8")
        assert "directives:" in content

    def test_settings_json_copied(self, fake_git_repo: Path) -> None:
        init_project(fake_git_repo)
        path = fake_git_repo / ".claude" / "settings.json"
        assert "hooks" in json.loads(path.read_text(encoding="utf-8"))


# ── Hooks Tests ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestHooks:
    """Test hook script copying."""

    EXPECTED_HOOKS = [
        "lib-trw.sh",
        "post-tool-event.sh",
        "pre-compact.sh",
        "session-end.sh",
        "session-start.sh",
        "stop-ceremony.sh",
        "subagent-start.sh",
        "task-completed-ceremony.sh",
        "validate-prd-write.sh",
    ]

    def test_all_hooks_copied(self, fake_git_repo: Path) -> None:
        init_project(fake_git_repo)
        hooks_dir = fake_git_repo / ".claude" / "hooks"

        copied = sorted(f.name for f in hooks_dir.iterdir() if f.suffix == ".sh")
        assert copied == self.EXPECTED_HOOKS

    def test_hooks_no_phase_check(self, fake_git_repo: Path) -> None:
        """post-phase-check.sh should NOT be deployed (tool removed)."""
        init_project(fake_git_repo)
        assert not (fake_git_repo / ".claude" / "hooks" / "post-phase-check.sh").exists()

    def test_hooks_not_empty(self, fake_git_repo: Path) -> None:
        init_project(fake_git_repo)
        hooks_dir = fake_git_repo / ".claude" / "hooks"

        for hook in self.EXPECTED_HOOKS:
            hook_file = hooks_dir / hook
            assert hook_file.stat().st_size > 0, f"Hook {hook} is empty"


# ── Skills Tests ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSkills:
    """Test skill directory deployment."""

    EXPECTED_SKILLS = [
        "deliver",
        "framework-check",
        "learn",
        "memory-audit",
        "memory-optimize",
        "prd-groom",
        "prd-new",
        "prd-review",
        "project-health",
        "sprint-finish",
        "sprint-init",
        "test-strategy",
    ]

    def test_init_deploys_skills(self, fake_git_repo: Path) -> None:
        """After init_project(), .claude/skills/ has 12 subdirectories each with SKILL.md."""
        result = init_project(fake_git_repo)
        assert not result["errors"]

        skills_dir = fake_git_repo / ".claude" / "skills"
        deployed = sorted(d.name for d in skills_dir.iterdir() if d.is_dir())
        assert deployed == self.EXPECTED_SKILLS

        for skill in self.EXPECTED_SKILLS:
            skill_md = skills_dir / skill / "SKILL.md"
            assert skill_md.is_file(), f"Missing SKILL.md in {skill}"
            assert skill_md.stat().st_size > 0, f"SKILL.md is empty in {skill}"

    def test_init_force_overwrites_skills(self, fake_git_repo: Path) -> None:
        """Write dummy SKILL.md, run init_project(force=True), verify content changed."""
        init_project(fake_git_repo)

        # Write dummy content to one skill
        dummy_path = fake_git_repo / ".claude" / "skills" / "deliver" / "SKILL.md"
        dummy_path.write_text("dummy content", encoding="utf-8")

        result = init_project(fake_git_repo, force=True)
        assert not result["errors"]

        restored = dummy_path.read_text(encoding="utf-8")
        assert restored != "dummy content"

    def test_init_skills_idempotent(self, fake_git_repo: Path) -> None:
        """Run init_project() twice — no errors and same file count."""
        result1 = init_project(fake_git_repo)
        assert not result1["errors"]

        result2 = init_project(fake_git_repo)
        assert not result2["errors"]

        # All skill files should be skipped on second run
        skills_dir = fake_git_repo / ".claude" / "skills"
        deployed = sorted(d.name for d in skills_dir.iterdir() if d.is_dir())
        assert deployed == self.EXPECTED_SKILLS


# ── Agents Tests ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestAgents:
    """Test agent file deployment."""

    EXPECTED_AGENTS = [
        "code-simplifier.md",
        "prd-groomer.md",
        "requirement-reviewer.md",
        "requirement-writer.md",
        "traceability-checker.md",
        "trw-implementer.md",
        "trw-researcher.md",
        "trw-reviewer.md",
        "trw-tester.md",
    ]

    def test_init_deploys_agents(self, fake_git_repo: Path) -> None:
        """After init_project(), .claude/agents/ has agent .md files."""
        result = init_project(fake_git_repo)
        assert not result["errors"]

        agents_dir = fake_git_repo / ".claude" / "agents"
        deployed = sorted(f.name for f in agents_dir.iterdir() if f.suffix == ".md")
        assert deployed == self.EXPECTED_AGENTS

        for agent in self.EXPECTED_AGENTS:
            agent_file = agents_dir / agent
            assert agent_file.stat().st_size > 0, f"Agent {agent} is empty"


# ── Bootstrap Config Flags — PRD-INFRA-011-FR06 ────────────────────────


@pytest.mark.unit
class TestBootstrapConfigFlags:
    """Tests for source_package and test_path bootstrap flags — PRD-INFRA-011-FR06."""

    def test_source_package_in_config(self, fake_git_repo: Path) -> None:
        """source_package='myapp' → config.yaml has source_package_name: myapp."""
        init_project(fake_git_repo, source_package="myapp")
        content = (fake_git_repo / ".trw" / "config.yaml").read_text(encoding="utf-8")
        assert "source_package_name: myapp" in content

    def test_test_path_in_config(self, fake_git_repo: Path) -> None:
        """test_path='tests' → config.yaml has tests_relative_path: tests."""
        init_project(fake_git_repo, test_path="tests")
        content = (fake_git_repo / ".trw" / "config.yaml").read_text(encoding="utf-8")
        assert "tests_relative_path: tests" in content

    def test_both_flags_in_config(self, fake_git_repo: Path) -> None:
        """Both flags → config.yaml has both fields."""
        init_project(fake_git_repo, source_package="myapp", test_path="tests")
        content = (fake_git_repo / ".trw" / "config.yaml").read_text(encoding="utf-8")
        assert "source_package_name: myapp" in content
        assert "tests_relative_path: tests" in content

    def test_default_no_extra_fields(self, fake_git_repo: Path) -> None:
        """No args → config.yaml does NOT have source_package_name or tests_relative_path."""
        init_project(fake_git_repo)
        content = (fake_git_repo / ".trw" / "config.yaml").read_text(encoding="utf-8")
        assert "source_package_name" not in content
        assert "tests_relative_path" not in content


# ── Update Project Tests ─────────────────────────────────────────────────


@pytest.fixture()
def initialized_repo(fake_git_repo: Path) -> Path:
    """Create a repo with TRW already initialized."""
    result = init_project(fake_git_repo)
    assert not result["errors"]
    return fake_git_repo


@pytest.mark.unit
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
        assert "v24.0_TRW" in content

    def test_updates_hooks(self, initialized_repo: Path) -> None:
        """Hook scripts are overwritten with latest versions."""
        hook_path = initialized_repo / ".claude" / "hooks" / "session-start.sh"
        hook_path.write_text("old hook", encoding="utf-8")

        update_project(initialized_repo)

        content = hook_path.read_text(encoding="utf-8")
        assert content != "old hook"

    def test_updates_skills(self, initialized_repo: Path) -> None:
        """Skill files are overwritten with latest versions."""
        skill_path = initialized_repo / ".claude" / "skills" / "deliver" / "SKILL.md"
        skill_path.write_text("old skill", encoding="utf-8")

        update_project(initialized_repo)

        content = skill_path.read_text(encoding="utf-8")
        assert content != "old skill"

    def test_updates_agents(self, initialized_repo: Path) -> None:
        """Agent files are overwritten with latest versions."""
        agent_path = initialized_repo / ".claude" / "agents" / "code-simplifier.md"
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


@pytest.mark.unit
class TestUpdateRemovesStaleArtifacts:
    """Test that update_project cleans up renamed/removed artifacts."""

    def test_removes_stale_hook(self, initialized_repo: Path) -> None:
        """Hook scripts not in bundled data are removed."""
        stale_hook = initialized_repo / ".claude" / "hooks" / "old-removed-hook.sh"
        stale_hook.write_text("#!/bin/sh\nexit 0", encoding="utf-8")

        result = update_project(initialized_repo)

        assert not stale_hook.exists()
        assert any("removed:" in u and "old-removed-hook" in u for u in result["updated"])

    def test_removes_stale_skill(self, initialized_repo: Path) -> None:
        """Skill directories not in bundled data are removed."""
        stale_skill = initialized_repo / ".claude" / "skills" / "old-skill"
        stale_skill.mkdir(parents=True, exist_ok=True)
        (stale_skill / "SKILL.md").write_text("old", encoding="utf-8")

        update_project(initialized_repo)

        assert not stale_skill.exists()

    def test_removes_stale_agent(self, initialized_repo: Path) -> None:
        """Agent files not in bundled data are removed."""
        stale_agent = initialized_repo / ".claude" / "agents" / "old-agent.md"
        stale_agent.write_text("old agent", encoding="utf-8")

        update_project(initialized_repo)

        assert not stale_agent.exists()

    def test_does_not_remove_non_md_agents(self, initialized_repo: Path) -> None:
        """Non-.md files in agents directory are not touched."""
        other_file = initialized_repo / ".claude" / "agents" / "notes.txt"
        other_file.write_text("user notes", encoding="utf-8")

        update_project(initialized_repo)

        assert other_file.exists()


@pytest.mark.unit
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
        assert "deliver" in deployed
        assert "learn" in deployed
        assert "project-health" in deployed

    def test_creates_new_agent(self, initialized_repo: Path) -> None:
        """New agents in bundled data are deployed."""
        result = update_project(initialized_repo)
        assert not result["errors"]

        agents_dir = initialized_repo / ".claude" / "agents"
        deployed = sorted(f.name for f in agents_dir.iterdir() if f.suffix == ".md")
        assert "trw-implementer.md" in deployed
        assert "trw-tester.md" in deployed
