"""Split bootstrap init/content tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.bootstrap import _DATA_DIR, init_project
from trw_mcp.models.config import TRWConfig

from ._bootstrap_test_support import fake_git_repo  # noqa: F401

# The EXACT deployed hook/agent set is environment-dependent: a standalone
# install (the public PyPI/GitHub mirror) legitimately installs the opt-in
# distill-channel hooks (pre-tool-distill-hint.sh, lib-distill-hint.sh) +
# trw-distill-explorer agent, which the monorepo dev-repo init does not. The
# EXPECTED_* lists below are the monorepo baseline, enforced in the monorepo CI.
# Skip the exact-equality assertions in the standalone mirror (no repo-root
# scripts/) where the superset is correct, not a regression.
_EXACT_SET_MONOREPO_ONLY = pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2] / "scripts").is_dir(),
    reason="exact hook/agent deploy set is env-dependent (distill channels install in standalone mirror); enforced in monorepo CI",
)


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
            ".trw/context/messages.yaml",
            ".trw/templates/claude_md.md",
            ".trw/config.yaml",
            ".trw/learnings/index.yaml",
            ".trw/.gitignore",
            ".claude/settings.json",
            ".mcp.json",
            "CLAUDE.md",
            "REVIEW.md",
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
        # Dirs don't report as created when they already exist.
        # Always-write files: framework-owned templates that are refreshed on
        # every init. Includes cursor-managed templates added in Sprint 91
        # (PRD-CORE-136 / PRD-CORE-137) — subagents, commands, skills mirror,
        # rules MDC, and hooks-related artifacts are re-rendered from bundled
        # templates on each init for idempotency. _extend_result(include_updated
        # =True) merges updated→created in the init flow, so re-renders show up
        # here.
        file_creates = [c for c in result2["created"] if not c.endswith("/")]
        expected_always_write = {
            ".mcp.json",
            "installer-meta.yaml",
            "managed-artifacts.yaml",
            "VERSION.yaml",
            # Cursor-managed templates (Sprint 91 — PRD-CORE-136 / 137)
            ".cursor/rules/",
            ".cursor/agents/",
            ".cursor/commands/",
            ".cursor/skills/",
            ".cursor/hooks/",
            ".cursor/hooks.json",
            ".cursor/cli.json",
            "AGENTS.md",
            # Distill-channel artifacts re-rendered from bundled templates on
            # every Claude Code install (CC-03 / CC-05 — see
            # bootstrap/_claude_code_distill_channels.py).
            ".claude/agents/trw-distill-explorer.md",
            ".claude/hooks/pre-tool-distill-hint.sh",
            ".claude/hooks/lib-distill-hint.sh",
            # CC loop.md — TRW-ceremony-aware /loop customization. Written on
            # first claude-code profile detection (settings.json presence triggers
            # claude-code in ide_targets on the second init-project run, so loop.md
            # may appear in "created" on what the caller sees as the "second" run).
            ".claude/loop.md",
        }
        unexpected = [c for c in file_creates if not any(e in c for e in expected_always_write)]
        assert len(unexpected) == 0, f"Unexpected creates: {unexpected}"

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
        assert TRWConfig().framework_version in content

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
        "completion-gate.sh",
        "helper-idle.sh",
        "instructions-loaded.sh",
        "lib-ide-adapter.sh",
        "lib-trw.sh",
        "phase-cycle-stop.sh",
        "post-compact.sh",
        "post-tool-event.sh",
        "pre-compact.sh",
        "pre-tool-deliver-gate.sh",
        "session-end.sh",
        "session-start.sh",
        "stop-ceremony.sh",
        "subagent-start.sh",
        "subagent-stop.sh",
        "user-prompt-submit.sh",
        "validate-prd-write.sh",
    ]

    @_EXACT_SET_MONOREPO_ONLY
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
        "trw-audit",
        "trw-ceremony-guide",
        "trw-code-search",
        "trw-commit",
        "trw-deliver",
        "trw-dry-check",
        "trw-exec-plan",
        "trw-feedback",
        "trw-framework-check",
        "trw-learn",
        "trw-memory-audit",
        "trw-memory-optimize",
        "trw-prd-groom",
        "trw-prd-new",
        "trw-prd-ready",
        "trw-prd-review",
        "trw-project-health",
        "trw-reflect",
        "trw-security-check",
        "trw-self-review",
        "trw-simplify",
        "trw-sprint-finish",
        "trw-sprint-init",
        "trw-sprint-team",
        "trw-team-playbook",
        "trw-test-strategy",
    ]

    def test_init_deploys_skills(self, fake_git_repo: Path) -> None:
        """After init_project(), .claude/skills/ has 26 subdirectories each with SKILL.md."""
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
        dummy_path = fake_git_repo / ".claude" / "skills" / "trw-deliver" / "SKILL.md"
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

    def test_email_template_skill_not_shipped(self, fake_git_repo: Path) -> None:
        """Regression: the `email-template` skill must NOT ship to user projects.

        `email-template` is a TRW-platform product feature (it scaffolds
        branded transactional HTML emails for ``backend/templates/email/``),
        not a framework engineering-memory capability. It was removed from
        the installer's bundled skill set; this test guards against a
        re-introduction.
        """
        init_project(fake_git_repo)
        skills_dir = fake_git_repo / ".claude" / "skills"
        deployed = {d.name for d in skills_dir.iterdir() if d.is_dir()}
        assert "email-template" not in deployed, (
            "email-template must not be bundled with the installer (non-TRW-framework skill)"
        )

    def test_bundled_source_excludes_email_template(self) -> None:
        """The canonical bundled-skills source dir must not contain email-template.

        Guards the source of truth the installer globs over
        (``trw-mcp/src/trw_mcp/data/skills/``) and the Codex client variant
        (``data/codex/skills/``), so a stray copy in either location is caught.
        """
        canonical = _DATA_DIR / "skills"
        codex = _DATA_DIR / "codex" / "skills"
        assert not (canonical / "email-template").exists(), (
            "email-template leaked back into the canonical bundled skills dir"
        )
        assert not (codex / "email-template").exists(), "email-template leaked back into the codex bundled skills dir"

    def test_every_shipped_skill_is_a_trw_framework_skill(self, fake_git_repo: Path) -> None:
        """Allowlist guard: every shipped skill must be a TRW framework skill.

        Catches ANY future stray non-framework skill (personal, experimental,
        product-feature) leaking into what users receive. TRW framework skills
        are namespaced ``trw-*``; the shipped set must match the curated
        EXPECTED_SKILLS allowlist exactly.
        """
        init_project(fake_git_repo)
        skills_dir = fake_git_repo / ".claude" / "skills"
        deployed = sorted(d.name for d in skills_dir.iterdir() if d.is_dir())

        # Every shipped skill carries the framework `trw-` namespace.
        non_framework = [name for name in deployed if not name.startswith("trw-")]
        assert not non_framework, f"non-TRW-framework skills leaked into the installer bundle: {non_framework}"

        # The shipped set matches the curated allowlist exactly — a new
        # skill (stray or intentional) forces this assertion to be updated,
        # surfacing the addition for review.
        assert deployed == self.EXPECTED_SKILLS, (
            "shipped skill set drifted from the TRW-framework allowlist; "
            f"unexpected: {sorted(set(deployed) - set(self.EXPECTED_SKILLS))}, "
            f"missing: {sorted(set(self.EXPECTED_SKILLS) - set(deployed))}"
        )


# ── Simplify Skill Content Tests ────────────────────────────────────────


@pytest.mark.unit
class TestSimplifySkillContent:
    """Test that bundled simplify skill contains only generic, portable content."""

    BUNDLED_SKILL_DIR = _DATA_DIR / "skills" / "trw-simplify"
    BUNDLED_SKILL = BUNDLED_SKILL_DIR / "SKILL.md"
    TRW_SPECIFIC_TERMS = ["lock_for_rmw", "TRWConfig", "trw://config", "FastMCP"]

    def test_bundled_simplify_skill_exists(self) -> None:
        """Bundled simplify SKILL.md exists at the expected path."""
        assert self.BUNDLED_SKILL.exists()

    def test_bundled_simplify_skill_is_generic(self) -> None:
        """Bundled simplify SKILL.md contains no trw-mcp-specific terms."""
        content = self.BUNDLED_SKILL.read_text(encoding="utf-8")
        for term in self.TRW_SPECIFIC_TERMS:
            assert term not in content, f"Found trw-mcp-specific term '{term}' in bundled skill"

    def test_bundled_simplify_has_preservation_rules(self) -> None:
        """Bundled simplify SKILL.md contains all 10 Preservation Rules."""
        content = self.BUNDLED_SKILL.read_text(encoding="utf-8")
        assert "Preservation Rules" in content
        for i in range(1, 11):
            assert f"{i}. **DO NOT" in content, f"Missing preservation rule {i}"

    def test_bundled_simplify_no_conventions_file(self) -> None:
        """Bundled simplify skill directory contains no conventions.md."""
        conventions_path = self.BUNDLED_SKILL_DIR / "conventions.md"
        assert not conventions_path.exists(), "conventions.md should not be bundled"


# ── Agents Tests ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestAgents:
    """Test agent file deployment.

    Adding a new agent? See TestAgentDefinitions docstring in test_bundled_agents.py
    for the full update sequence (7 locations).
    """

    EXPECTED_AGENTS = [
        "trw-adversarial-auditor.md",
        "trw-auditor.md",
        "trw-code-simplifier.md",
        "trw-implementer.md",
        "trw-lead.md",
        "trw-prd-groomer.md",
        "trw-requirement-reviewer.md",
        "trw-requirement-writer.md",
        "trw-researcher.md",
        "trw-reviewer.md",
        "trw-tester.md",
        "trw-traceability-checker.md",
    ]

    @_EXACT_SET_MONOREPO_ONLY
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
