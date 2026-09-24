"""Keep ceremony-guide variants aligned with the v26.1 lifecycle contract."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.bootstrap._client_skills import render_skill_md

DATA = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data"
_RENDERED_CLIENTS = ("codex", "copilot", "opencode")
GUIDES = (
    "skills/trw-ceremony-guide/SKILL.md",
    "codex/skills/trw-ceremony-guide/SKILL.md",
    "copilot/skills/trw-ceremony-guide/SKILL.md",
)


def _read(relative: str) -> str:
    """Read *relative*, rendering codex/copilot/opencode variants from the canonical skill.

    codex/copilot/opencode no longer ship SKILL.md forks (PRD-CORE-291-FR04);
    ``render_skill_md`` only reduces frontmatter keys, so the body text these
    assertions check is identical to the canonical source.
    """
    parts = relative.split("/")
    if len(parts) == 4 and parts[0] in _RENDERED_CLIENTS and parts[1] == "skills" and parts[3] == "SKILL.md":
        client, _, skill_name, _ = parts
        canonical = (DATA / "skills" / skill_name / "SKILL.md").read_text(encoding="utf-8")
        return render_skill_md(canonical, client)
    return (DATA / relative).read_text(encoding="utf-8")


def test_ceremony_guides_preserve_rigid_minimal_phases() -> None:
    for relative in GUIDES:
        content = _read(relative)
        assert "| MINIMAL | IMPLEMENT, VALIDATE, DELIVER |" in content, relative
        assert "VALIDATE is never skipped" in content, relative
        assert "session_start -> inspect/work -> project-native validation" in content, relative


def test_ceremony_guides_describe_tool_boundaries_truthfully() -> None:
    for relative in GUIDES:
        content = _read(relative)
        assert "It does not run tests, lint, types, builds, or coverage" in content, relative
        assert "command_results" in content, relative
        assert "It does not promote learnings into `AGENTS.md`" in content, relative
        assert "failed_command" in content and "expiry_iso" in content, relative
        assert "substantive review" in content and "cold-context self-review" in content, relative
        assert "durable discovery" in content and "not routine status" in content, relative
        assert "promotes learnings to AGENTS.md" not in content, relative


def test_ceremony_guides_do_not_repeat_unsupported_empirical_multipliers() -> None:
    for relative in GUIDES:
        content = _read(relative)
        assert "3x fewer" not in content, relative
        assert "2x rework" not in content, relative
        assert "Sprint 26 had 6" not in content, relative
