"""Tests for bundled and root agent-definition compatibility contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_bundle_asset_support import _MONOREPO_CLAUDE, _resolve_data_path


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
        return {"bundled": agents_dir / agent_name, "root": root_agents_dir / agent_name}

    @staticmethod
    def _assert_variants_include_snippets(variant_paths: dict[str, Path], required_snippets: list[str]) -> None:
        """Assert every variant contains each required snippet.

        PRD-QUAL-073 FR10 (Route B): bundled agent files carry ``{tool:trw_X}``
        placeholders; ``.claude/agents/`` copies are the expanded form. This
        helper expands markers before snippet matching so contract tests pass
        against both variants.
        """
        import re as _re

        marker_re = _re.compile(r"\{tool:(trw_\w+)\}")
        for variant_name, path in variant_paths.items():
            raw = path.read_text(encoding="utf-8")
            content = marker_re.sub(lambda m: m.group(1), raw)
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
        """Bundled and .claude/ variants align after marker expansion + tier resolution.

        - PRD-QUAL-073 FR10 (Route B): bundled carries ``{tool:trw_X}``
          placeholders that get expanded to bare ``trw_X``.
        - PRD-INFRA-104 FR-04: bundled also carries capability tiers in
          ``model:`` that get resolved to Claude Code shortnames
          (``frontier->opus``, ``balanced->sonnet``, ``local-small->haiku``).

        After both transforms the dev-repo ``.claude/agents/`` copy must
        match byte-for-byte. If this test fails, run
        ``python3 scripts/sync-agents.py`` (without ``--check``).
        """
        import re as _re

        from trw_mcp.agents.tier_resolver import rewrite_model_line

        bundled_raw = (agents_dir / agent_name).read_text(encoding="utf-8")
        bundled_expanded = _re.sub(r"\{tool:(trw_\w+)\}", lambda m: m.group(1), bundled_raw)
        bundled_resolved = rewrite_model_line(bundled_expanded, client="claude-code")
        root_content = (root_agents_dir / agent_name).read_text(encoding="utf-8")

        assert bundled_resolved == root_content, (
            f"{agent_name}: .claude/agents/ drifts from bundled source after marker "
            "expansion + tier resolution. Run scripts/sync-agents.py to regenerate."
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
        required_snippets = ["Pre-Implementation Checklist (PRD-QUAL-056-FR03)"]

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
            ("trw-reviewer.md", "balanced"),
            ("trw-researcher.md", "balanced"),
            # Restored 2026-05-05 by PRD-INFRA-104 FR-05/FR-06 once the
            # capability-tier resolver translates frontier -> opus at
            # install time. Prior to that fix these were dropped in
            # commit 20fb923e7 because the harness rejected the raw
            # tier value.
            ("trw-implementer.md", "frontier"),
            ("trw-prd-groomer.md", "frontier"),
        ],
    )
    def test_agent_model_assignment(self, agents_dir: Path, agent_name: str, expected_model: str) -> None:
        """Agent definition specifies correct model shortname."""
        import yaml

        content = (agents_dir / agent_name).read_text(encoding="utf-8")
        _, frontmatter, _ = content.split("---", 2)
        meta = yaml.safe_load(frontmatter)
        assert meta["model"] == expected_model

    @pytest.mark.parametrize("agent_name", ["trw-auditor.md", "trw-reviewer.md", "trw-researcher.md"])
    def test_readonly_agents_no_write(self, agents_dir: Path, agent_name: str) -> None:
        """Read-only agents have Write and Edit in disallowedTools."""
        import yaml

        content = (agents_dir / agent_name).read_text(encoding="utf-8")
        _, frontmatter, _ = content.split("---", 2)
        meta = yaml.safe_load(frontmatter)
        disallowed = meta.get("disallowedTools", [])
        assert "Write" in disallowed, f"{agent_name}: Write must be disallowed"
        assert "Edit" in disallowed, f"{agent_name}: Edit must be disallowed"

    def test_implementer_has_edit(self, agents_dir: Path) -> None:
        """Implementer agent has Edit and Write in tools list."""
        import yaml

        content = (agents_dir / "trw-implementer.md").read_text(encoding="utf-8")
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
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if line.strip() == "</output>":
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
        """Agent definitions must have name, description in frontmatter.

        ``model`` is optional: agents that omit it inherit the harness default.
        Agents that DO pin a tier must use one of the valid capability tiers.

        PRD-INFRA-104 (2026-05-05): Once the capability-tier resolver lands
        in ``trw_mcp.agents.tier_resolver`` and is wired into
        ``_install_agents`` + ``scripts/sync-agents.py``, the bundle pins
        survive translation into the client-specific harness vocabulary.
        ``trw-implementer``, ``trw-lead``, ``trw-prd-groomer`` are pinned
        to ``frontier`` and resolve to ``opus`` at install time for the
        Claude Code client profile.
        """
        import yaml

        content = (agents_dir / agent_name).read_text(encoding="utf-8")
        _, frontmatter, _ = content.split("---", 2)
        meta = yaml.safe_load(frontmatter)
        assert "name" in meta, f"{agent_name}: missing 'name'"
        assert "description" in meta, f"{agent_name}: missing 'description'"
        if "model" in meta:
            valid_models = ("frontier", "balanced", "local-large", "local-small")
            assert meta["model"] in valid_models, (
                f"{agent_name}: model must be one of {valid_models}, got {meta['model']}"
            )
