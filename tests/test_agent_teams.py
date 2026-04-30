"""Tests for retired beta team compatibility shims, hooks, rendering, and settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# ── Shared path resolution ──────────────────────────────────────────────
# Package data lives in src/trw_mcp/data/ (canonical source).
# The monorepo also copies these to .claude/ at root. Tests prefer
# package data so they work in both monorepo and standalone contexts.

_TESTS_DIR = Path(__file__).parent
_PKG_DATA = _TESTS_DIR.parent / "src" / "trw_mcp" / "data"
_MONOREPO_CLAUDE = _TESTS_DIR.parent.parent / ".claude"


def _resolve_data_path(pkg_subdir: str, monorepo_subdir: str) -> Path:
    """Resolve a data path, preferring package data over monorepo location."""
    pkg = _PKG_DATA / pkg_subdir
    if pkg.exists():
        return pkg
    mono = _MONOREPO_CLAUDE / monorepo_subdir
    if mono.exists():
        return mono
    pytest.skip(f"{pkg_subdir} not found in package data or monorepo")


from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md import (
    render_agent_teams_protocol,
    render_template,
)

_CFG = TRWConfig()


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def _get_tools() -> dict[str, Any]:
    """Create fresh server and return tool map."""
    from fastmcp import FastMCP

    from trw_mcp.tools.learning import register_learning_tools

    srv = FastMCP("test")
    register_learning_tools(srv)
    return get_tools_sync(srv)


class TestRenderAgentTeamsProtocol:
    """Tests for the retired beta team compatibility shim."""

    @pytest.mark.parametrize("enabled", [True, False])
    def test_shim_is_empty_even_if_legacy_flag_is_set(self, enabled: bool) -> None:
        """v25 keeps the public symbol but emits no beta protocol body."""
        with patch(
            "trw_mcp.state.claude_md._static_sections.get_config", return_value=TRWConfig(agent_teams_enabled=enabled)
        ):
            result = render_agent_teams_protocol()

        assert result == ""


class TestAgentTeamsTemplateIntegration:
    """Tests for legacy template placeholder behavior."""

    def test_template_placeholder_replaced(self) -> None:
        """{{agent_teams_section}} placeholder is correctly replaced by supplied context."""
        template = "before\n{{agent_teams_section}}after"
        context = {"agent_teams_section": ""}
        result = render_template(template, context)
        assert "{{agent_teams_section}}" not in result
        assert result == "before\nafter"

    def test_bundled_template_has_placeholder_support(self) -> None:
        """Bundled claude_md.md template keeps compact placeholders for renderer compatibility."""
        data_dir = Path(__file__).parent.parent / "src" / "trw_mcp" / "data" / "templates"
        bundled = data_dir / "claude_md.md"
        assert bundled.exists(), "Bundled template must exist"
        content = bundled.read_text(encoding="utf-8")
        assert "{{imperative_opener}}" in content

    def test_full_sync_succeeds_without_beta_agent_teams(self, tmp_path: Path) -> None:
        """trw_claude_md_sync succeeds and omits retired beta team protocol content."""
        trw_dir = tmp_path / _CFG.trw_dir
        trw_dir.mkdir(parents=True, exist_ok=True)
        (trw_dir / _CFG.learnings_dir / _CFG.entries_dir).mkdir(parents=True, exist_ok=True)

        tools = _get_tools()
        with patch(
            "trw_mcp.state.claude_md._static_sections.get_config", return_value=TRWConfig(agent_teams_enabled=True)
        ):
            result = tools["trw_claude_md_sync"].fn(scope="root")

        assert result["status"] == "synced"
        content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "trw:start" in content
        assert "beta team Protocol" not in content
        assert "TeamCreate" not in content


class TestAgentTeamsConfig:
    """Tests for agent_teams_enabled compatibility field."""

    def test_default_disabled(self) -> None:
        """agent_teams_enabled defaults to False in v25."""
        config = TRWConfig()
        assert config.agent_teams_enabled is False

    def test_env_override_still_parses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRW_AGENT_TEAMS_ENABLED remains accepted for legacy config compatibility."""
        monkeypatch.setenv("TRW_AGENT_TEAMS_ENABLED", "true")
        config = TRWConfig()
        assert config.agent_teams_enabled is True


class TestSettingsJson:
    """Tests for .claude/settings.json hook registrations.

    These tests validate the monorepo's settings.json. When running in the
    standalone public repo (no .claude/settings.json at repo root), tests
    are skipped — the equivalent validation happens in test_bootstrap.py
    via init-project.
    """

    @pytest.fixture()
    def settings_path(self) -> Path:
        """Return path to settings.json (monorepo only — skips in standalone)."""
        path = _MONOREPO_CLAUDE / "settings.json"
        if not path.exists():
            pytest.skip("settings.json not present (standalone repo — tested via init-project)")
        return path

    def test_settings_exists(self, settings_path: Path) -> None:
        """settings.json exists."""
        assert settings_path.exists()

    def test_settings_valid_json(self, settings_path: Path) -> None:
        """settings.json is valid JSON."""
        import json

        content = settings_path.read_text(encoding="utf-8")
        data = json.loads(content)
        assert isinstance(data, dict)

    def test_beta_agent_team_hooks_not_registered(self, settings_path: Path) -> None:
        """v25 settings do not register retired beta team hook events."""
        import json

        data = json.loads(settings_path.read_text(encoding="utf-8"))
        hooks = data.get("hooks", {})
        assert "TeammateIdle" not in hooks
        assert "TaskCompleted" not in hooks

    def test_agent_teams_env_var_not_set(self, settings_path: Path) -> None:
        """v25 settings do not opt into the retired beta team env var."""
        import json

        data = json.loads(settings_path.read_text(encoding="utf-8"))
        env = data.get("env", {})
        assert "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in env


class TestAgentDefinitions:
    """Tests for .claude/agents/ helper definitions.

    Adding a new agent? Update these locations in order:
    1. Create `.claude/agents/{name}.md` (YAML frontmatter + markdown body)
    2. Copy to `trw-mcp/src/trw_mcp/data/agents/{name}.md` (bundled for pip install)
       — or run `scripts/sync-data.sh` which copies .claude/agents/ -> data/agents/
    3. Add to parametrized lists below (test_agent_file_exists, test_agent_model_assignment,
       test_agent_no_stray_tags, test_agent_has_required_frontmatter) + role-specific tests
    4. Add to `TestAgents.EXPECTED_AGENTS` in `test_bootstrap.py`
    5. Update agent count in `test_manifest_lists_all_bundled_artifacts` in `test_bootstrap.py`
    6. Keep bundled/root variants aligned via scripts/sync-agents.py
    7. Use capability-tier frontmatter labels (`frontier`, `balanced`, `local-small`) in v25
    """

    @pytest.fixture()
    def agents_dir(self) -> Path:
        """Return path to bundled agent definitions."""
        return _resolve_data_path("agents", "agents")

    @pytest.fixture()
    def root_agents_dir(self) -> Path:
        """Return path to monorepo root agent definitions when available."""
        agents_dir = _MONOREPO_CLAUDE / "agents"
        if not agents_dir.exists():
            pytest.skip("root .claude/agents not available in this environment")
        return agents_dir

    @staticmethod
    def _variant_paths(agents_dir: Path, root_agents_dir: Path, agent_name: str) -> dict[str, Path]:
        """Return bundled/root paths for an audit agent pair."""
        return {
            "bundled": agents_dir / agent_name,
            "root": root_agents_dir / agent_name,
        }

    @staticmethod
    def _assert_variants_include_snippets(variant_paths: dict[str, Path], required_snippets: list[str]) -> None:
        """Assert every variant contains each required snippet.

        PRD-QUAL-073 FR10 (Route B): bundled agent files carry ``{tool:trw_X}``
        placeholders; ``.claude/agents/`` copies are the expanded form. This
        helper expands markers before snippet matching so contract tests pass
        against both variants.
        """
        import re as _re

        _marker_re = _re.compile(r"\{tool:(trw_\w+)\}")
        for variant_name, path in variant_paths.items():
            raw = path.read_text(encoding="utf-8")
            content = _marker_re.sub(lambda m: m.group(1), raw)
            for snippet in required_snippets:
                assert snippet in content, f"{variant_name} {path.name} missing snippet: {snippet}"

    @pytest.mark.parametrize(
        "agent_name",
        [
            "trw-auditor.md",
            "trw-implementer.md",
            "trw-prd-groomer.md",
            "trw-reviewer.md",
            "trw-researcher.md",
        ],
    )
    def test_agent_file_exists(self, agents_dir: Path, agent_name: str) -> None:
        """Agent definition file exists."""
        assert (agents_dir / agent_name).exists(), f"{agent_name} must exist"

    @pytest.mark.parametrize(
        ("agent_name", "required_snippets"),
        [
            (
                "trw-auditor.md",
                [
                    "label in `legacy_category` on the finding.",
                    "note it as a finding with `category: spec_gap`",
                    "legacy_category: prd-ambiguity|spec-gap|type-safety|dry|error-handling|observability|test-quality|integration|null",
                ],
            ),
            (
                "trw-adversarial-auditor.md",
                [
                    "label in `legacy_category` on the finding.",
                    "note it as a finding with `category: spec_gap`",
                    "legacy_category: prd-ambiguity|spec-gap|type-safety|dry|error-handling|observability|test-quality|integration|null",
                ],
            ),
        ],
    )
    def test_bundled_audit_agents_include_legacy_taxonomy_contract(
        self,
        agents_dir: Path,
        agent_name: str,
        required_snippets: list[str],
    ) -> None:
        """Bundled audit agents expose the legacy taxonomy compatibility contract."""
        import re as _re

        raw = (agents_dir / agent_name).read_text(encoding="utf-8")
        bundled_content = _re.sub(r"\{tool:(trw_\w+)\}", lambda m: m.group(1), raw)
        for snippet in required_snippets:
            assert snippet in bundled_content

    @pytest.mark.parametrize("agent_name", ["trw-auditor.md", "trw-adversarial-auditor.md"])
    def test_audit_agent_prompt_pairs_match_root_sources(
        self,
        agents_dir: Path,
        root_agents_dir: Path,
        agent_name: str,
    ) -> None:
        """Bundled and .claude/ variants align after marker expansion (PRD-QUAL-073 FR10, Route B).

        Bundled is the source of truth and carries ``{tool:trw_X}`` placeholders
        so it renders correctly across client profiles. ``.claude/agents/`` is
        the dev-repo-local marker-expanded copy (bare ``trw_X`` tool names).
        The two must match byte-for-byte after expanding the bundled markers.
        """
        import re as _re

        bundled_raw = (agents_dir / agent_name).read_text(encoding="utf-8")
        bundled_expanded = _re.sub(r"\{tool:(trw_\w+)\}", lambda m: m.group(1), bundled_raw)
        root_content = (root_agents_dir / agent_name).read_text(encoding="utf-8")

        assert bundled_expanded == root_content, (
            f"{agent_name}: .claude/agents/ drifts from bundled source after marker "
            "expansion. Run scripts/sync-agents.py to regenerate."
        )

    @pytest.mark.parametrize("agent_name", ["trw-auditor.md", "trw-adversarial-auditor.md"])
    def test_audit_agent_variants_include_finding_taxonomy_contract(
        self,
        agents_dir: Path,
        root_agents_dir: Path,
        agent_name: str,
    ) -> None:
        """Root and bundled audit agents retain the FR07 finding taxonomy contract."""
        required_snippets = [
            "category: spec_gap",
            "category: spec_gap|impl_gap|test_gap|integration_gap|traceability_gap",
            "legacy_category: prd-ambiguity|spec-gap|type-safety|dry|error-handling|observability|test-quality|integration|null",
        ]

        self._assert_variants_include_snippets(
            self._variant_paths(agents_dir, root_agents_dir, agent_name),
            required_snippets,
        )

    @pytest.mark.parametrize("agent_name", ["trw-auditor.md", "trw-adversarial-auditor.md"])
    def test_audit_agent_variants_include_prior_learning_recall_contract(
        self,
        agents_dir: Path,
        root_agents_dir: Path,
        agent_name: str,
    ) -> None:
        """Root and bundled audit agents retain the FR08 prior learning recall contract."""

        required_snippets = [
            "**Check for prior domain learnings (PRD-QUAL-056-FR08):**",
            "Call `trw_recall(query='<prd-domain> audit-finding')`",
            'Note them in audit context as "known patterns to watch for"',
            "prior_learning_verification:",
            "known_patterns: []",
            "verified_patterns: []",
            "missed_patterns: []",
        ]

        self._assert_variants_include_snippets(
            self._variant_paths(agents_dir, root_agents_dir, agent_name),
            required_snippets,
        )

    @pytest.mark.parametrize("agent_name", ["trw-auditor.md", "trw-adversarial-auditor.md"])
    def test_audit_agent_variants_include_preflight_self_review_contract(
        self,
        agents_dir: Path,
        root_agents_dir: Path,
        agent_name: str,
    ) -> None:
        """Root and bundled audit agents retain the FR03/FR05 preflight verification contract."""

        required_snippets = [
            "Check `events.jsonl` for `pre_implementation_checklist_complete` and `pre_audit_self_review`",
            "preflight_verification:",
            "self_review_alignment: matches|underreported|missing",
        ]

        self._assert_variants_include_snippets(
            self._variant_paths(agents_dir, root_agents_dir, agent_name),
            required_snippets,
        )

    @pytest.mark.parametrize("agent_name", ["trw-auditor.md", "trw-adversarial-auditor.md"])
    def test_audit_agent_variants_include_learning_capture_contract(
        self,
        agents_dir: Path,
        root_agents_dir: Path,
        agent_name: str,
    ) -> None:
        """Root and bundled audit agents retain the FR06 learning-capture contract."""

        required_snippets = [
            "For each P0 or P1 finding, call `trw_learn()` with:",
            '- `tags`: ["audit-finding", "{prd-id}", "{finding-category}"]',
            "- `phase_affinity`: Determined by finding category per taxonomy table",
        ]

        self._assert_variants_include_snippets(
            self._variant_paths(agents_dir, root_agents_dir, agent_name),
            required_snippets,
        )

    def test_implementer_agent_variants_include_fr03_checklist_contract(
        self,
        agents_dir: Path,
        root_agents_dir: Path,
    ) -> None:
        """Root and bundled implementer agents retain the FR03 pre-implementation checklist contract.

        Note: trw_preflight_log was removed from the MCP tool surface (14-tool reduction).
        The checklist guidance itself remains in the agent prompt.
        """

        required_snippets = [
            "Pre-Implementation Checklist (PRD-QUAL-056-FR03)",
        ]

        self._assert_variants_include_snippets(
            self._variant_paths(agents_dir, root_agents_dir, "trw-implementer.md"),
            required_snippets,
        )

    @pytest.mark.parametrize("agent_name", ["trw-auditor.md", "trw-adversarial-auditor.md"])
    def test_audit_agent_variants_include_verdict_exit_criteria_and_escalation_contract(
        self,
        agents_dir: Path,
        root_agents_dir: Path,
        agent_name: str,
    ) -> None:
        """Root and bundled audit agents retain the FR11 verdict exit criteria contract."""
        required_snippets = [
            "| **PASS** | Zero P0 findings AND zero P1 findings AND all FRs have verdict PASS or PARTIAL-with-justification |",
            "| **CONDITIONAL** | Zero P0 findings AND 1-2 P1 findings that are fixable without architectural change |",
            "| **FAIL** | Any P0 finding OR 3+ P1 findings OR any FR with verdict MISSING |",
            "Maximum audit cycles before escalation: 3 (configurable via `.trw/config.yaml` field `max_audit_cycles`, default 3).",
            "audit_angles_completed: [spec, vision, types, dry, errors, observability, integration, tests, traceability]",
            "# PASS: zero P0, zero P1, and every FR is PASS or PARTIAL-with-justification",
            "# CONDITIONAL: zero P0 and 1-2 P1 findings fixable without architectural change",
            "# FAIL: any P0, 3+ P1 findings, or any FR verdict MISSING",
        ]

        self._assert_variants_include_snippets(
            self._variant_paths(agents_dir, root_agents_dir, agent_name),
            required_snippets,
        )

    @pytest.mark.parametrize(
        ("agent_name", "expected_model"),
        [
            ("trw-auditor.md", "balanced"),
            ("trw-implementer.md", "frontier"),
            ("trw-prd-groomer.md", "frontier"),
            ("trw-reviewer.md", "balanced"),
            ("trw-researcher.md", "balanced"),
        ],
    )
    def test_agent_model_assignment(self, agents_dir: Path, agent_name: str, expected_model: str) -> None:
        """Agent definition specifies correct model shortname."""
        import yaml

        content = (agents_dir / agent_name).read_text(encoding="utf-8")
        _, frontmatter, _ = content.split("---", 2)
        meta = yaml.safe_load(frontmatter)
        assert meta["model"] == expected_model

    @pytest.mark.parametrize(
        "agent_name",
        ["trw-auditor.md", "trw-reviewer.md", "trw-researcher.md"],
    )
    def test_readonly_agents_no_write(self, agents_dir: Path, agent_name: str) -> None:
        """Read-only agents have Write and Edit in disallowedTools."""
        content = (agents_dir / agent_name).read_text(encoding="utf-8")
        import yaml

        _, frontmatter, _ = content.split("---", 2)
        meta = yaml.safe_load(frontmatter)
        disallowed = meta.get("disallowedTools", [])
        assert "Write" in disallowed, f"{agent_name}: Write must be disallowed"
        assert "Edit" in disallowed, f"{agent_name}: Edit must be disallowed"

    def test_implementer_has_edit(self, agents_dir: Path) -> None:
        """Implementer agent has Edit and Write in tools list."""
        content = (agents_dir / "trw-implementer.md").read_text(encoding="utf-8")
        import yaml

        _, frontmatter, _ = content.split("---", 2)
        meta = yaml.safe_load(frontmatter)
        tools = meta.get("tools", [])
        assert "Edit" in tools, "trw-implementer: Edit must be in tools"
        assert "Write" in tools, "trw-implementer: Write must be in tools"

    @pytest.mark.parametrize(
        "agent_name",
        [
            "trw-auditor.md",
            "trw-implementer.md",
            "trw-prd-groomer.md",
            "trw-reviewer.md",
            "trw-researcher.md",
        ],
    )
    def test_agent_no_stray_tags(self, agents_dir: Path, agent_name: str) -> None:
        """Agent definitions must not contain stray XML closing tags."""
        content = (agents_dir / agent_name).read_text(encoding="utf-8")
        # Check for orphan </output> tags outside of code blocks
        lines = content.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == "</output>":
                raise AssertionError(f"{agent_name} line {i + 1}: stray </output> tag")

    @pytest.mark.parametrize(
        "agent_name",
        [
            "trw-auditor.md",
            "trw-implementer.md",
            "trw-prd-groomer.md",
            "trw-reviewer.md",
            "trw-researcher.md",
        ],
    )
    def test_agent_has_required_frontmatter(self, agents_dir: Path, agent_name: str) -> None:
        """Agent definitions must have name, description, model in frontmatter."""
        content = (agents_dir / agent_name).read_text(encoding="utf-8")
        import yaml

        _, frontmatter, _ = content.split("---", 2)
        meta = yaml.safe_load(frontmatter)
        assert "name" in meta, f"{agent_name}: missing 'name'"
        assert "description" in meta, f"{agent_name}: missing 'description'"
        assert "model" in meta, f"{agent_name}: missing 'model'"
        valid_models = ("frontier", "balanced", "local-large", "local-small")
        assert meta["model"] in valid_models, f"{agent_name}: model must be one of {valid_models}, got {meta['model']}"


class TestSkillDefinitions:
    """Tests for flywheel skill contract alignment across root and bundled copies."""

    @pytest.fixture()
    def skills_dir(self) -> Path:
        """Return path to bundled skill definitions."""
        return _resolve_data_path("skills", "skills")

    @pytest.fixture()
    def root_skills_dir(self) -> Path:
        """Return path to monorepo root skill definitions when available."""
        skills_dir = _MONOREPO_CLAUDE / "skills"
        if not skills_dir.exists():
            pytest.skip("root .claude/skills not available in this environment")
        return skills_dir

    def test_exec_plan_skill_matches_root_source(self, skills_dir: Path, root_skills_dir: Path) -> None:
        """Bundled exec-plan skill stays byte-for-byte aligned with root source."""
        assert (skills_dir / "trw-exec-plan" / "SKILL.md").read_text(encoding="utf-8") == (
            root_skills_dir / "trw-exec-plan" / "SKILL.md"
        ).read_text(encoding="utf-8")

    def test_self_review_skill_matches_root_source(self, skills_dir: Path, root_skills_dir: Path) -> None:
        """Bundled self-review skill stays byte-for-byte aligned with root source."""
        assert (skills_dir / "trw-self-review" / "SKILL.md").read_text(encoding="utf-8") == (
            root_skills_dir / "trw-self-review" / "SKILL.md"
        ).read_text(encoding="utf-8")

    def test_audit_skill_matches_root_source(self, skills_dir: Path, root_skills_dir: Path) -> None:
        """Bundled audit skill stays byte-for-byte aligned with root source."""
        assert (skills_dir / "trw-audit" / "SKILL.md").read_text(encoding="utf-8") == (
            root_skills_dir / "trw-audit" / "SKILL.md"
        ).read_text(encoding="utf-8")

    def test_sprint_finish_skill_matches_root_source(self, skills_dir: Path, root_skills_dir: Path) -> None:
        """Bundled sprint-finish skill stays byte-for-byte aligned with root source."""
        assert (skills_dir / "trw-sprint-finish" / "SKILL.md").read_text(encoding="utf-8") == (
            root_skills_dir / "trw-sprint-finish" / "SKILL.md"
        ).read_text(encoding="utf-8")

    def test_skill_variants_include_preflight_logging_contract(self, skills_dir: Path, root_skills_dir: Path) -> None:
        """Root and bundled skill variants retain the pre-implementation checklist/self-review contract.

        Note: trw_preflight_log was removed from the MCP tool surface (14-tool reduction).
        Tests verify the checklist concept and self-review structure remain, not the removed tool call.
        """
        variant_paths = {
            "root_exec_plan": root_skills_dir / "trw-exec-plan" / "SKILL.md",
            "bundled_exec_plan": skills_dir / "trw-exec-plan" / "SKILL.md",
            "codex_exec_plan": _PKG_DATA / "codex" / "skills" / "trw-exec-plan" / "SKILL.md",
            "root_self_review": root_skills_dir / "trw-self-review" / "SKILL.md",
            "bundled_self_review": skills_dir / "trw-self-review" / "SKILL.md",
            "root_audit": root_skills_dir / "trw-audit" / "SKILL.md",
            "bundled_audit": skills_dir / "trw-audit" / "SKILL.md",
            "codex_audit": _PKG_DATA / "codex" / "skills" / "trw-audit" / "SKILL.md",
            "copilot_audit": _PKG_DATA / "copilot" / "skills" / "trw-audit" / "SKILL.md",
            "root_sprint_finish": root_skills_dir / "trw-sprint-finish" / "SKILL.md",
            "bundled_sprint_finish": skills_dir / "trw-sprint-finish" / "SKILL.md",
            "codex_sprint_finish": _PKG_DATA / "codex" / "skills" / "trw-sprint-finish" / "SKILL.md",
        }
        required_snippets = {
            "exec_plan": [
                "Pre-Implementation Checklist (PRD-QUAL-056-FR03)",
            ],
            "self_review": [
                "Pre-Audit Self-Review Skill (PRD-QUAL-056-FR05)",
            ],
            "audit": [
                "Check `events.jsonl` for `pre_implementation_checklist_complete` and `pre_audit_self_review`",
                "preflight_verification:",
                "self_review_alignment: matches|underreported|missing",
                "prior_learning_verification:",
            ],
            "sprint_finish": [
                "Delivery ceremony",
                "Learnings promoted",
            ],
        }

        for variant_name, path in variant_paths.items():
            content = path.read_text(encoding="utf-8")
            skill_kind = (
                "exec_plan"
                if "exec_plan" in variant_name
                else "self_review"
                if "self_review" in variant_name
                else "sprint_finish"
                if "sprint_finish" in variant_name
                else "audit"
            )
            for snippet in required_snippets[skill_kind]:
                assert snippet in content, f"{variant_name} missing snippet: {snippet}"
