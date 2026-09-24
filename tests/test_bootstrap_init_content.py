"""Split bootstrap init/content tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._ide_detection_isolation import isolate_ide_detection
from trw_mcp.bootstrap import _DATA_DIR, init_project
from trw_mcp.models.config import TRWConfig

from ._bootstrap_test_support import fake_git_repo  # noqa: F401
from .test_bootstrap_update_core import resolve_instruction_text


@pytest.fixture(autouse=True)
def _isolate_ide_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Detect clients from ``tmp_path`` only — see ``tests/_ide_detection_isolation``."""
    isolate_ide_detection(monkeypatch)


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
            ".trw/frameworks/AARE-F-FRAMEWORK.md",
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
        # S4: one installed document per canon; the compiled views are retired.
        for retired in ("FRAMEWORK-CORE.md", "FRAMEWORK-REFERENCE.md", "AARE-F-CORE.md", "AARE-F-REFERENCE.md"):
            assert not (fake_git_repo / ".trw" / "frameworks" / retired).exists(), retired

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
        # The client is pinned: since PRD-CORE-252-FR03 each client's agents go
        # to its own destination, so an unpinned first run (which resolves
        # through ``detect_ide``, and therefore through the developer's PATH)
        # can select a different client than the second run does once `.claude/`
        # exists — and every agent then legitimately reports as created twice.
        init_project(fake_git_repo, ide="claude-code")
        result2 = init_project(fake_git_repo, ide="claude-code")

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
            # Receipt-bound canon bodies are atomically re-verified/replaced
            # with VERSION.yaml on every init.
            "FRAMEWORK.md",
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
        # Protocol reachable through the carrier (inline, or a resolved @-import).
        assert "trw_session_start" in resolve_instruction_text(claude_md)


# ── Validation Tests ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidation:
    """Test error handling and validation."""

    def test_not_git_repo_warns_loudly_and_still_installs_framework(self, tmp_path: Path) -> None:
        """A non-git target installs the FULL framework and is still told it is not a git repo.

        PRD-INFRA-170 FR06 (trw-mcp 0.63.0) replaced the pre-0.63.0 contract
        where a missing ``.git/`` was a blocking ``errors`` entry that aborted
        the install. That gate produced the reported defect: config.yaml +
        .mcp.json written, ``.trw/frameworks/*`` bodies absent, MCP connected
        and looking healthy while the methodology the tools implement was
        missing. The framework-body deploy is git-independent and idempotent,
        so it no longer gates on the git check.

        The condition was NOT dropped, only re-routed from a blocking error to
        a non-blocking warning. The PRD locks it as "framework bodies deployed
        OR a loud, non-silent warning — never a silent half-install"; the
        implementation delivers both. This test pins both halves, and pins BOTH
        surfacing channels so a regression that silently swallows the condition
        fails here:

        1. ``result["warnings"]`` — rendered by the ``init-project`` CLI
           (``server/_subcommands.py`` "Warnings:" block) and logged in -v mode.
        2. the ``project_init_non_git`` structlog event.
        """
        import structlog

        with structlog.testing.capture_logs() as logs:
            result = init_project(tmp_path)

        # ── The framework IS deployed — the actual subject of the 0.63.0 fix.
        # The four bodies named in the changelog must exist AND be non-empty:
        # the reported failure was files missing, and a zero-byte body would be
        # the same half-install wearing a passing existence check.
        for body in (
            "FRAMEWORK.md",
            "AARE-F-FRAMEWORK.md",
            "VERSION.yaml",
            "DEPLOYMENT.json",
        ):
            path = tmp_path / ".trw" / "frameworks" / body
            assert path.is_file(), f"framework body not deployed in non-git target: {body}"
            assert path.stat().st_size > 0, f"framework body deployed empty in non-git target: {body}"
        assert result["created"], "non-git install created nothing"

        # ── Missing .git/ is no longer blocking: it must not appear as an error.
        assert result["errors"] == [], result["errors"]

        # ── But the user IS still told, through the channel the CLI renders.
        warnings = result.get("warnings", [])
        git_warnings = [w for w in warnings if "not a git repository" in w]
        assert len(git_warnings) == 1, warnings
        # Names the offending target and the remediation, not just the symptom.
        assert str(tmp_path) in git_warnings[0]
        assert "git init" in git_warnings[0]

        # ── Second channel: the structlog event. Asserted independently so
        # dropping either surface is caught, not masked by the other.
        assert any(entry.get("event") == "project_init_non_git" for entry in logs), [
            entry.get("event") for entry in logs
        ]

    def test_git_repo_gets_no_non_git_warning(self, fake_git_repo: Path) -> None:
        """The warning is condition-specific — a real git repo must not see it.

        Guards the companion failure mode of the test above: a warning appended
        unconditionally would satisfy every "is the user told" assertion while
        telling every user the same thing, making the signal worthless.
        """
        result = init_project(fake_git_repo)
        assert result["errors"] == [], result["errors"]
        assert not [w for w in result.get("warnings", []) if "not a git repository" in w]


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
        claude_md = fake_git_repo / "CLAUDE.md"

        content = claude_md.read_text(encoding="utf-8")
        assert "trw_session_start" in content
        assert "trw_deliver" in content

    def test_claude_md_carries_the_protocol_inline(self, fake_git_repo: Path) -> None:
        """PRD-QUAL-143-FR01: the block is inline, with no ``@`` import or sidecar.

        ``ide`` is explicit because ``detect_ide`` reads ``shutil.which("cursor")``,
        a machine-global signal.
        """
        init_project(fake_git_repo, ide="claude-code")
        raw = (fake_git_repo / "CLAUDE.md").read_text(encoding="utf-8")

        assert [ln for ln in raw.splitlines() if ln.strip().startswith("@")] == []
        assert "trw_session_start" in raw
        assert not (fake_git_repo / ".trw" / "INSTRUCTIONS.md").exists()

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
        "instructions-loaded.sh",
        "lib-intent-guard.sh",  # PRD-CORE-250 FR05
        "lib-trw.sh",
        "post-compact.sh",
        "post-tool-degenerate-result.sh",  # PRD-CORE-250 FR06
        "post-tool-event.sh",
        "post-tool-intent-check.sh",  # PRD-SEC-013 FR07
        "pre-compact.sh",
        "pre-tool-deliver-gate.sh",
        "pre-tool-intent-guard.sh",  # PRD-SEC-013 FR05
        "session-end.sh",
        "session-start.sh",
        "stop-ceremony.sh",
        "subagent-start.sh",
        "subagent-stop.sh",
        "user-prompt-submit.sh",
    ]
    #: Opt-in CC-03 pair. A bundled hook ships only when it is registered, or sourced
    #: by a registered hook, so these ship (and register) only when the feature is on.
    CC03_HOOKS = frozenset({"lib-distill-hint.sh", "pre-tool-distill-hint.sh"})

    @_EXACT_SET_MONOREPO_ONLY
    def test_all_hooks_copied(self, fake_git_repo: Path) -> None:
        # Pin the client: ambient Cursor detection omits Claude-only CC03 files.
        init_project(fake_git_repo, ide="claude-code")
        hooks_dir = fake_git_repo / ".claude" / "hooks"

        copied = sorted(f.name for f in hooks_dir.iterdir() if f.suffix == ".sh")
        # CC-03 is off by default, so its pair neither ships nor registers.
        assert copied == sorted(self.EXPECTED_HOOKS)
        assert all(name not in self._registered(fake_git_repo) for name in self.CC03_HOOKS)

    @staticmethod
    def _registered(repo: Path) -> str:
        settings = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
        return json.dumps(settings.get("hooks", {}))

    def test_cc03_pair_ships_registered_when_enabled_and_leaves_when_disabled(self, fake_git_repo: Path) -> None:
        from trw_mcp.bootstrap import update_project

        init_project(fake_git_repo, ide="claude-code")
        config = fake_git_repo / ".trw" / "config.yaml"
        base = config.read_text(encoding="utf-8")
        hooks_dir = fake_git_repo / ".claude" / "hooks"

        config.write_text(base + "cc03_hook_enabled: true\n", encoding="utf-8")
        update_project(fake_git_repo, ide="claude-code")
        assert all((hooks_dir / name).is_file() for name in self.CC03_HOOKS)
        assert "pre-tool-distill-hint.sh" in self._registered(fake_git_repo)

        config.write_text(base + "cc03_hook_enabled: false\n", encoding="utf-8")
        update_project(fake_git_repo, ide="claude-code")
        assert not any((hooks_dir / name).exists() for name in self.CC03_HOOKS)
        assert "pre-tool-distill-hint.sh" not in self._registered(fake_git_repo)

    def test_update_removes_a_hook_the_installer_wrote_and_no_longer_ships(self, fake_git_repo: Path) -> None:
        import hashlib

        from trw_mcp.bootstrap import update_project

        init_project(fake_git_repo, ide="claude-code")
        hooks_dir = fake_git_repo / ".claude" / "hooks"
        retired, edited = hooks_dir / "validate-prd-write.sh", hooks_dir / "retired-and-edited.sh"
        retired.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
        edited.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        manifest = fake_git_repo / ".trw" / "managed-artifacts.yaml"
        recorded = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (retired, edited)}
        edited.write_text("#!/bin/sh\n# my local change\nexit 0\n", encoding="utf-8")
        text = manifest.read_text(encoding="utf-8")
        entries = "".join(f"  {name}: {digest}\n" for name, digest in recorded.items())
        manifest.write_text(text.replace("content_hashes:\n", "content_hashes:\n" + entries, 1), encoding="utf-8")

        result = update_project(fake_git_repo, ide="claude-code")

        assert not retired.exists(), "an unedited hook TRW wrote and no longer ships must be removed"
        assert edited.exists(), "a user-edited copy is never deleted"
        assert any("retired-and-edited.sh" in w for w in result.get("warnings", [])), result.get("warnings")

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
        "trw-assess",
        "trw-audit",
        "trw-ceremony-guide",
        "trw-code-search",
        "trw-commit",
        "trw-delegate",
        "trw-deliver",
        "trw-dry-check",
        "trw-exec-plan",
        "trw-feedback",
        "trw-framework-check",
        "trw-learn",
        "trw-memory-audit",
        "trw-memory-optimize",
        "trw-plan-review",
        "trw-prd-groom",
        "trw-prd-new",
        "trw-prd-ready",
        "trw-prd-review",
        "trw-project-health",
        "trw-reflect",
        "trw-security-check",
        "trw-self-review",
        "trw-sprint-finish",
        "trw-sprint-init",
        "trw-test-strategy",
    ]

    def _installed_by_default(self) -> list[str]:
        """The allowlist minus opt-in skills whose feature is off by default (trw-assess)."""
        from trw_mcp.bootstrap._optional_skills import CONDITIONAL_SKILLS

        return [s for s in self.EXPECTED_SKILLS if s not in CONDITIONAL_SKILLS]

    def test_init_deploys_skills(self, fake_git_repo: Path) -> None:
        """After init_project(), .claude/skills/ has 26 subdirectories each with SKILL.md."""
        result = init_project(fake_git_repo)
        assert not result["errors"]

        skills_dir = fake_git_repo / ".claude" / "skills"
        deployed = sorted(d.name for d in skills_dir.iterdir() if d.is_dir())
        assert deployed == self._installed_by_default()

        for skill in self._installed_by_default():
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
        assert deployed == self._installed_by_default()

    def test_email_template_skill_not_shipped(self, fake_git_repo: Path) -> None:
        """Regression: the `email-template` skill must NOT ship to user projects.

        `email-template` is a TRW-platform product feature (it scaffolds
        branded transactional HTML emails for the proprietary platform's
        server-side email templates), not a framework engineering-memory
        capability. It was removed from
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

        Every client (codex, copilot, opencode) now renders from this one
        corpus (PRD-CORE-291-FR04), so a single absence check on the
        canonical dir covers every client's projection.
        """
        canonical = _DATA_DIR / "skills"
        assert not (canonical / "email-template").exists(), (
            "email-template leaked back into the canonical bundled skills dir"
        )

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
        assert deployed == self._installed_by_default(), (
            "shipped skill set drifted from the TRW-framework allowlist; "
            f"unexpected: {sorted(set(deployed) - set(self.EXPECTED_SKILLS))}, "
            f"missing: {sorted(set(self.EXPECTED_SKILLS) - set(deployed))}"
        )


@pytest.mark.unit
class TestDryCheckSkillContent:
    """The read-only duplicate scan must not prescribe unsafe extraction."""

    def test_duplicate_matches_are_evidence_aware_candidates(self) -> None:
        from trw_mcp.bootstrap._client_skills import canonical_skills_dir, render_skill_md

        required = (
            "evidence, not a verdict",
            "Required client projections",
            "**consolidate**",
            "**retain**",
            "**uncertain**",
            "reduces net complexity",
            "justified no-change result",
        )
        canonical_text = (canonical_skills_dir() / "trw-dry-check" / "SKILL.md").read_text(encoding="utf-8")
        for client in ("codex", "copilot", "opencode"):
            content = render_skill_md(canonical_text, client)
            assert "duplicated code blocks that violate DRY principles" not in content
            assert "For each duplicated block, suggest" not in content
            for guidance in required:
                assert guidance in content, (
                    f"{client} rendering is missing duplicate-classification guidance: {guidance}"
                )


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
        "trw-implementer.md",
        "trw-lead.md",
        "trw-prd-groomer.md",
        "trw-requirement-reviewer.md",
        "trw-researcher.md",
        "trw-reviewer.md",
    ]

    @_EXACT_SET_MONOREPO_ONLY
    def test_init_deploys_agents(self, fake_git_repo: Path) -> None:
        """After a claude-code init_project(), .claude/agents/ has agent .md files.

        PRD-CORE-252-FR03: ``.claude/agents`` is claude-code's destination, not
        a universal one, so the client is named rather than inferred.
        """
        result = init_project(fake_git_repo, ide="claude-code")
        assert not result["errors"]

        agents_dir = fake_git_repo / ".claude" / "agents"
        deployed = sorted(f.name for f in agents_dir.iterdir() if f.suffix == ".md")
        assert deployed == self.EXPECTED_AGENTS

        for agent in self.EXPECTED_AGENTS:
            agent_file = agents_dir / agent
            assert agent_file.stat().st_size > 0, f"Agent {agent} is empty"


# Bootstrap Config Flags (PRD-INFRA-011-FR06) test class removed under
# PRD-CORE-291 (slice 2): the --source-package/--test-path flags and the
# source_package_name/tests_relative_path config fields they populated had
# no reader anywhere, so the flags, the init_project()/`_default_config()`
# parameters, and this test class were all removed together.


def _all_clients() -> list[str]:
    """Every built-in profile, derived from the registry so a new client is covered."""
    from trw_mcp.models.config._profiles import _PROFILES

    return sorted(_PROFILES)


def _declared_carrier_clients() -> list[str]:
    """Clients whose ``instruction_path`` is their carrier.

    claude-code declares ``.claude/INSTRUCTIONS.md``, which no writer produces
    (see ``client_profiles/catalog.py``); its carrier is CLAUDE.md, asserted by
    ``test_claude_code_target_gets_the_inline_block``.
    """
    return [client for client in _all_clients() if client != "claude-code"]


class TestEveryClientGetsAnInlineBlock:
    """PRD-QUAL-143-FR01: the TRW block is inline for every client.

    The ``.trw/INSTRUCTIONS.md`` sidecar and its ``@`` import are retired, so no
    install may emit an import, and each client's declared carrier must hold the
    protocol and the deliver gate itself.
    """

    @staticmethod
    def _shape(root: Path) -> tuple[list[str], bool]:
        text = (root / "CLAUDE.md").read_text(encoding="utf-8")
        imports = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("@")]
        return imports, "trw_session_start" in text

    def test_claude_code_target_gets_the_inline_block(self, fake_git_repo: Path) -> None:
        init_project(fake_git_repo, ide="claude-code")

        from trw_mcp.state.claude_md.sections._tool_lifecycle import DELIVER_GATE_PHRASE

        imports, inline = self._shape(fake_git_repo)
        assert imports == []
        assert inline
        assert DELIVER_GATE_PHRASE in (fake_git_repo / "CLAUDE.md").read_text(encoding="utf-8")
        assert not (fake_git_repo / ".trw" / "INSTRUCTIONS.md").exists()

    @pytest.mark.parametrize("client", _all_clients())
    def test_no_client_gets_an_import(self, fake_git_repo: Path, client: str) -> None:
        """A codex-only selection writes no CLAUDE.md at all (PRD-CORE-262-FR05)."""
        init_project(fake_git_repo, ide=client)

        if not (fake_git_repo / "CLAUDE.md").exists():
            return
        imports, _ = self._shape(fake_git_repo)
        assert imports == [], f"{client} got {imports}"

    @pytest.mark.parametrize("client", _declared_carrier_clients())
    def test_declared_instruction_surface_carries_the_protocol(self, fake_git_repo: Path, client: str) -> None:
        """The protocol must arrive in the file this client actually reads.

        This is the contract the previous version of this test *meant* to assert
        and got wrong. It read ``CLAUDE.md`` specifically, so when bootstrap
        correctly stopped scaffolding the TRW block into a file that codex,
        opencode and cursor-cli never load, the test reported those three clients
        as having lost the protocol. They had not: each one's own carrier had it
        the whole time. The test was pinned to a carrier, not to an outcome.

        Pinning it to the outcome means resolving the carrier the same way
        production does — from ``write_targets.instruction_path`` — so the
        assertion follows the protocol wherever the framework decides to put it,
        and fails only when it genuinely fails to arrive.

        No client has a sidecar to fall back on, so a dangling or empty carrier
        is total loss for that client, not degraded service.
        """
        from trw_mcp.models.config._profiles import resolve_client_profile

        declared = resolve_client_profile(client).write_targets.instruction_path
        assert declared, f"{client} declares no instruction_path — nothing can carry its protocol"

        init_project(fake_git_repo, ide=client)

        carrier = fake_git_repo / declared
        assert carrier.is_file(), f"{client} declares {declared} but the install never wrote it"

        text = resolve_instruction_text(carrier)
        assert "trw_session_start" in text, (
            f"{client} must still receive the protocol somewhere it can read; "
            f"its declared carrier {declared} does not name the entry point"
        )

    # The antigravity-cli strict xfail that used to live here did its job exactly
    # as designed: it went RED (XPASS) the moment the gate was added to
    # render_antigravity_instructions, instead of quietly outliving the defect.
    # Marker removed because the case now genuinely passes — every
    # client states the gate verbatim, with no exemptions.
    @pytest.mark.parametrize("client", _declared_carrier_clients())
    def test_declared_instruction_surface_states_the_deliver_gate(self, fake_git_repo: Path, client: str) -> None:
        """Arrival is not enough — the carrier must state the gate verbatim.

        ``trw_session_start`` appearing somewhere proves a TRW block was written.
        It does not prove the block still contains the one rule that makes it a
        *protocol* carrier. No client has a sidecar or a fallback, so a
        block that names the tools but drops the gate is a surface that looks
        installed and licenses an unverified delivery.
        """
        from trw_mcp.models.config._profiles import resolve_client_profile
        from trw_mcp.state.claude_md.sections._tool_lifecycle import DELIVER_GATE_PHRASE

        init_project(fake_git_repo, ide=client)

        declared = resolve_client_profile(client).write_targets.instruction_path
        carrier = fake_git_repo / declared
        assert carrier.is_file(), f"{client} declares {declared} but the install never wrote it"
        text = resolve_instruction_text(carrier)

        assert DELIVER_GATE_PHRASE in text, (
            f"{client}'s carrier {declared} omits the deliver gate — the client is "
            "left with an instruction file that names the tools but not the rule"
        )

    def test_capability_comes_from_requested_targets_not_post_install_detection(self, fake_git_repo: Path) -> None:
        """Detection is unusable here; the resolved targets are authoritative.

        Installation creates both ``.claude/`` and ``.cursor/`` artifacts, and
        ``detect_ide`` additionally sets cursor-ide from ``shutil.which("cursor")``
        — a machine-global signal. If capability were read from detection, the
        answer would depend on what is installed on the build box.
        """
        init_project(fake_git_repo, ide="cursor-ide")
        # PRD-INFRA-192 FR09: an explicit cursor-ide install no longer scaffolds
        # Claude Code's .claude/, so detection has even less to go on; the
        # resolved targets still decide.
        assert not (fake_git_repo / ".claude").exists()

        imports, inline = self._shape(fake_git_repo)
        assert imports == [], "post-install .claude/ must not add an import"
        # No inline block either. cursor-ide's protocol lives in the file Cursor
        # documents and always applies; the CLAUDE.md copy was redundant. The
        # distinction that unblocked this — "chose cursor-ide" vs "`which cursor`
        # succeeded" — is now expressible because install records only an explicit
        # --ide or a client with an on-disk marker.
        assert not inline

        rule = (fake_git_repo / ".cursor" / "rules" / "trw-ceremony.mdc").read_text(encoding="utf-8")
        assert "alwaysApply: true" in rule
        assert "trw_session_start" in rule


@pytest.mark.integration
def test_claude_skills_install_when_claude_code_is_not_the_first_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``.claude/skills`` gate follows claude-code's profile, not the first-listed client's.

    ``TRWConfig.client_profile`` is the FIRST ``target_platforms`` entry. With
    opencode listed first, its profile (no skills) gated the Claude Code skill
    install off, so a project targeting claude-code got an empty
    ``.claude/skills`` on init and update (PRD-INFRA-192 FR06).
    """
    import trw_mcp.models.config as config_mod

    (tmp_path / ".git").mkdir()
    config = TRWConfig(target_platforms=["opencode", "claude-code"])
    monkeypatch.setattr(config_mod, "get_config", lambda: config)

    result = init_project(tmp_path, ide="all")

    assert not result["errors"], result["errors"]
    assert (tmp_path / ".claude" / "skills" / "trw-deliver" / "SKILL.md").is_file()
