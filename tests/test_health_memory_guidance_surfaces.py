"""Protect operational-health and learning-memory guidance from schema folklore."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.bootstrap._client_skills import render_skill_md

DATA = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data"
_RENDERED_CLIENTS = ("codex", "copilot", "opencode")


def _read(*relatives: str) -> list[tuple[str, str]]:
    """Read each *relative* path, rendering client variants from the canonical skill.

    ``codex``/``copilot``/``opencode`` no longer ship their own SKILL.md forks
    (PRD-CORE-291-FR04) -- every client renders from ``data/skills`` via
    ``render_skill_md``, which only reduces frontmatter keys, so the body text
    these tests assert on is identical to the canonical source.
    """
    results = []
    for relative in relatives:
        parts = relative.split("/")
        if len(parts) == 4 and parts[0] in _RENDERED_CLIENTS and parts[1] == "skills" and parts[3] == "SKILL.md":
            client, _, skill_name, _ = parts
            canonical = (DATA / "skills" / skill_name / "SKILL.md").read_text(encoding="utf-8")
            content = render_skill_md(canonical, client)
        else:
            content = (DATA / relative).read_text(encoding="utf-8")
        results.append((relative, content))
    return results


def test_framework_checks_resolve_live_state_without_fixed_memory_targets() -> None:
    variants = _read(
        "skills/trw-framework-check/SKILL.md",
        "codex/skills/trw-framework-check/SKILL.md",
        "opencode/skills/trw-framework-check/SKILL.md",
    )
    for relative, content in variants:
        assert "20-40 active" not in content, relative
        assert "index.yaml `last_updated`" not in content, relative
        assert "build_gate_ready" in content, relative
        assert "review_gate_ready" in content, relative
        assert "deliver_gate_summary" in content, relative
        assert "Do not discover runs through guessed" in content, relative
        assert "never inspect pin files" in content, relative
    for relative, content in variants[:2]:
        assert "status itself does not return a path" in content.lower(), relative
        assert "UNKNOWN" in content, relative
        assert "cannot prove run/session compliance" in content, relative


def test_project_health_uses_optional_schema_checked_sources() -> None:
    variants = _read(
        "skills/trw-project-health/SKILL.md",
        "codex/skills/trw-project-health/SKILL.md",
        "copilot/skills/trw-project-health/SKILL.md",
    )
    forbidden = (
        "{task_root}/*/runs/*",
        "promoted_to_claude_md",
        "Ceremony success rate | >= 80%",
        "Checkpoint frequency | >= 2/hr",
        "reflection_complete` immediately after `run_init` (< 60s gap)",
    )
    for relative, content in variants:
        for phrase in forbidden:
            assert phrase not in content, (relative, phrase)
        assert "UNKNOWN/NOT EMITTED" in content, relative
        assert "status itself does not return a path" in content.lower(), relative
        assert "not promotion into client instruction files" in content, relative


def test_memory_audit_discloses_retrieval_side_effects_and_partial_evidence() -> None:
    variants = _read(
        "skills/trw-memory-audit/SKILL.md",
        "codex/skills/trw-memory-audit/SKILL.md",
    )
    for relative, content in variants:
        assert "recall/session-start retrieval updates access telemetry" in content, relative
        assert "never use post-recall access metadata as staleness evidence" in content, relative
        assert "Never run wildcard `max_results=0`" in content, relative
        assert "SAMPLED/PARTIAL" in content and "UNKNOWN" in content, relative
        assert "distinct domain count" not in content, relative


def test_memory_optimization_uses_confirmed_tool_mutations_not_instruction_sync() -> None:
    variants = _read(
        "skills/trw-memory-optimize/SKILL.md",
        "codex/skills/trw-memory-optimize/SKILL.md",
    )
    for relative, content in variants:
        assert "Require explicit user confirmation" in content, relative
        # PRD-CORE-291 merged trw_learn_update into trw_learn's update mode.
        assert "Prefer `trw_learn(learning_id=...)`" in content, relative
        assert "do not hard-delete learning storage" in content, relative
        assert "Do not call it for that purpose" in content, relative
        assert "entries-per-domain formula" in content, relative
        assert "ALWAYS run `trw_instructions_sync`" not in content, relative
        assert "Use it for planning only" in content, relative
        assert "Do not invoke `trw-distill maintain optimize --apply`" in content, relative
        assert "applying an immutable reviewed receipt" in content, relative


def test_learn_reflection_uses_targeted_deduplication() -> None:
    variants = _read(
        "skills/trw-learn/SKILL.md",
        "codex/skills/trw-learn/SKILL.md",
        "copilot/skills/trw-learn/SKILL.md",
    )
    for relative, content in variants:
        assert 'trw_recall(query="<candidate keywords>")' in content, relative
        assert "detail` field is replaced, not appended automatically" in content, relative
        assert "pass the complete replacement with all still-valid detail and provenance" in content, relative
        assert "pass a refinement or reason fragment expecting append semantics" in content, relative
        assert "avoid blanket wildcard recall" in content, relative
        assert "\n/learn\n" not in content, relative
