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

    @pytest.mark.parametrize("agent_name", ["trw-auditor.md"])
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

    def test_adversarial_auditor_is_a_thin_lens_adapter(self, agents_dir: Path) -> None:
        """The adapter keeps its red-team vocabulary and its read-only grant.

        PRD-QUAL-128 moved this test's structural claims into
        ``test_audit_protocol_contracts.py::test_adapter_headings_are_disjoint_from_the_base``:
        heading disjointness now covers EVERY section the base owns rather than
        the two that happened to be listed, and the 500/900-word budgets moved
        with it. What stays here are the two literals worth pinning — domain
        vocabulary bound to Section C item 11, the Potemkin-gate class — plus the
        frontmatter contracts that make this agent read-only.
        """
        import yaml

        adapter = (agents_dir / "trw-adversarial-auditor.md").read_text(encoding="utf-8")
        _, frontmatter, body = adapter.split("---", 2)
        meta = yaml.safe_load(frontmatter)

        for phrase in ("property reachability", "Potemkin gate"):
            assert phrase in body, f"the adapter no longer red-teams NFR item 11: {phrase!r}"
        assert "LSP" in meta["tools"]
        assert "Bash" in meta["disallowedTools"]

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

    @pytest.mark.parametrize("agent_name", ["trw-reviewer.md", "trw-researcher.md"])
    def test_routing_descriptions_stay_concise(self, agents_dir: Path, agent_name: str) -> None:
        """Discovery metadata should route the agent, not duplicate its playbook."""
        import yaml

        content = (agents_dir / agent_name).read_text(encoding="utf-8")
        _, frontmatter, _ = content.split("---", 2)
        description = yaml.safe_load(frontmatter)["description"]
        assert len(description.split()) <= 60
        assert "<example>" not in description

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
        stray_lines = [i + 1 for i, line in enumerate(lines) if line.strip() == "</output>"]
        assert not stray_lines, f"{agent_name} lines {stray_lines}: stray </output> tag"

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
