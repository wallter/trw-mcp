"""Cross-client parity for the /trw-reflect skill (PRD-CORE-187 FR01).

The end-of-session reflection skill must be distributable to every client that
ships a bundled slash-command skill subset. The generic ``.claude/skills/``
path is covered by ``test_bundled_skills.py::test_reflect_skill_matches_root_source``;
this module locks in parity for the curated **codex**, **copilot**,
**opencode**, and **copilot-plugin** bundled skill subsets, plus the
cursor-IDE curated list.

Beyond existence, every copy must preserve the skill's load-bearing contract:
the approval gate, the ``.trw/reflections/`` ledger path, the canon-protection
guardrail, and the four implementation routes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap._cursor_ide import _IDE_CURATED_SKILLS
from trw_mcp.bootstrap._utils import _DATA_DIR
from trw_mcp.models.skill_manifest import validate_skill_markdown

# Bundled source copies (one per client variant) that must each ship a valid
# trw-reflect skill carrying the full behavioral contract.
_BUNDLED_REFLECT_SOURCES: tuple[Path, ...] = (
    _DATA_DIR / "skills" / "trw-reflect" / "SKILL.md",
    _DATA_DIR / "codex" / "skills" / "trw-reflect" / "SKILL.md",
    _DATA_DIR / "copilot" / "skills" / "trw-reflect" / "SKILL.md",
    _DATA_DIR / "opencode" / "skills" / "trw-reflect" / "SKILL.md",
    _DATA_DIR / "copilot" / "plugin" / "skills" / "trw-reflect" / "SKILL.md",
)

# Contract strings every client copy must retain (behavior, not existence).
_CONTRACT_MARKERS: tuple[str, ...] = (
    ".trw/reflections/",  # FR05 ledger path
    "approval",  # FR04 approval gate
    "quick-fix",  # FR04 route 1
    "trw_learn",  # FR04 route 3
    "improvement-backlog.md",  # FR03 dedup + FR04 route 4
    "CONSTITUTION.md",  # FR06 canon-protection guardrail
    "recurred",  # FR05 recurrence escalation
    "immutable active run/session ID",
    "A task-name slug alone never proves ledger ownership",
    "Run/session-id: <immutable identity>",
)


@pytest.mark.parametrize("skill_md", _BUNDLED_REFLECT_SOURCES, ids=lambda p: str(p.relative_to(_DATA_DIR)))
def test_bundled_reflect_copy_is_valid_and_carries_contract(skill_md: Path) -> None:
    """Each bundled trw-reflect copy validates and keeps the behavioral contract."""
    assert skill_md.exists(), f"trw-reflect SKILL.md missing at {skill_md}"
    content = skill_md.read_text(encoding="utf-8")
    result = validate_skill_markdown(content, path=skill_md, mode="compat")
    assert result.ok, f"SKILL.md failed validation: {[e.reason for e in result.errors]}"
    assert result.manifest is not None
    assert result.manifest.name == "trw-reflect"
    for marker in _CONTRACT_MARKERS:
        assert marker in content, f"trw-reflect at {skill_md} lost contract marker {marker!r}"


def test_reflect_in_cursor_ide_curated_list() -> None:
    """The cursor-IDE curated skill list distributes trw-reflect."""
    assert "trw-reflect" in _IDE_CURATED_SKILLS


def test_reflect_native_command_surfaces() -> None:
    """Clients with a native command surface ship a trw-reflect command.

    OpenCode commands (.opencode/commands) and cursor-IDE commands
    (.cursor/commands via _TRW_COMMANDS + template) are the two command
    surfaces the bootstrap installs today; both must carry trw-reflect and
    point at the skill workflow rather than restating it.
    """
    from trw_mcp.bootstrap._cursor_ide import _TRW_COMMANDS

    opencode_cmd = _DATA_DIR / "opencode" / "commands" / "trw-reflect.md"
    assert opencode_cmd.exists(), "opencode trw-reflect command missing"
    assert "trw-reflect" in opencode_cmd.read_text(encoding="utf-8")

    cursor_template = _DATA_DIR / "cursor_ide" / "commands" / "trw-reflect.md"
    assert cursor_template.exists(), "cursor-IDE trw-reflect command template missing"
    assert "/trw-reflect" in cursor_template.read_text(encoding="utf-8")
    assert "trw-reflect" in {name for name, _ in _TRW_COMMANDS}


def test_client_variants_drop_claude_specific_tool_names() -> None:
    """Codex/copilot/opencode variants must not name Claude-only tools."""
    for client in ("codex", "copilot", "opencode"):
        content = (_DATA_DIR / client / "skills" / "trw-reflect" / "SKILL.md").read_text(encoding="utf-8")
        assert "AskUserQuestion" not in content, f"{client} variant names Claude-specific AskUserQuestion"


def test_reflect_prd_fallback_is_repository_discovered() -> None:
    for skill_md in _BUNDLED_REFLECT_SOURCES:
        content = skill_md.read_text(encoding="utf-8")
        assert "repository's discovered PRD instructions and search-scope contract" in content
        assert "docs/requirements-aare-f/CLAUDE.md" not in content


def test_shared_reflect_contract_keeps_portable_debt_and_handoff_rules() -> None:
    content = (_DATA_DIR / "skills" / "trw-reflect" / "SKILL.md").read_text(encoding="utf-8")
    for phrase in (
        "count-reflection-debt.py` when present",
        "list free-form ledgers you could not parse",
        "redacted observed evidence",
        "A ledger-only pointer is insufficient",
    ):
        assert phrase in content
    for historical_detail in (
        "four different totals across 2026-06/07",
        "highest-yield signal class of the 2026-07-10 reflection",
        "PRD-CORE-216",
        "FPI-7 precedent",
        "CORE-216/217 + QUAL-117/118",
    ):
        assert historical_detail not in content


def test_generic_delta_mode_requires_exact_session_identity() -> None:
    content = (_DATA_DIR / "skills" / "trw-reflect" / "SKILL.md").read_text(encoding="utf-8")
    assert "recorded run/session ID equals the current immutable identity" in content
    assert "exact path was created earlier in the current conversation" in content
    assert "same run task-name slug" not in content


def test_prd_qual_120_fr07(tmp_path: Path) -> None:
    """FR07 acceptance: Given an action targets a draft PRD, missing target, or
    verified implementation, When counted, Then only verified implementation
    closes and each other item has a reason."""
    from trw_mcp.state.reflection_followthrough import reconcile_debt

    prds = tmp_path / "prds"
    prds.mkdir()
    (prds / "PRD-CORE-080.md").write_text(
        "---\nprd:\n  id: PRD-CORE-080\n  title: T\n  status: draft\n---\n", encoding="utf-8"
    )
    (prds / "PRD-CORE-081.md").write_text(
        "---\nprd:\n  id: PRD-CORE-081\n  title: T\n  status: implemented\n  functionality_level: live\n---\n",
        encoding="utf-8",
    )
    actions = [
        {"action_id": "a-draft", "state": "approved", "target_prd": "PRD-CORE-080"},
        {"action_id": "a-missing", "state": "approved", "target_prd": "PRD-CORE-404"},
        {"action_id": "a-verified", "state": "routed", "target_prd": "PRD-CORE-081"},
    ]
    open_debt, closed = reconcile_debt(actions, prds)
    assert [item.action_id for item in closed] == ["a-verified"]
    assert {item.action_id: item.reason for item in open_debt} == {
        "a-draft": "target_not_implemented",
        "a-missing": "target_missing",
    }
    # Every open item carries a typed reason.
    assert all(item.reason for item in open_debt)


def test_qual_120_typed_debt_cli_and_mirror_lifecycle(tmp_path: Path) -> None:
    """Audit F5: the --typed CLI mode is exercised end-to-end as a subprocess,
    and every trw-reflect mirror carries the typed-lifecycle doctrine."""
    import json
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[2] / "scripts" / "count-reflection-debt.py"
    prds = tmp_path / "prds"
    prds.mkdir()
    (prds / "PRD-CORE-090.md").write_text(
        "---\nprd:\n  id: PRD-CORE-090\n  title: T\n  status: draft\n---\n", encoding="utf-8"
    )
    actions = tmp_path / "actions.json"
    actions.write_text(
        json.dumps([{"action_id": "a1", "state": "approved", "target_prd": "PRD-CORE-090"}]),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(script), "--typed", str(actions), "--prds-dir", str(prds)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "open=1 closed=0" in result.stdout
    assert "reason=target_not_implemented" in result.stdout

    root = Path(__file__).resolve().parents[2]
    # Glob-discovered (auditor follow-up): every trw-reflect mirror that EXISTS
    # anywhere in the repo is asserted — a future mirror can never drift silently.
    mirrors = sorted(
        set(root.glob(".agents/skills/trw-reflect/SKILL.md"))
        | set(root.glob(".claude/skills/trw-reflect/SKILL.md"))
        | set((root / "trw-mcp" / "src" / "trw_mcp" / "data").rglob("trw-reflect/SKILL.md"))
    )
    assert len(mirrors) >= 7, mirrors  # the currently-known mirror population
    for mirror in mirrors:
        content = mirror.read_text(encoding="utf-8")
        assert "Typed follow-through lifecycle (PRD-QUAL-120-FR06)" in content, mirror
        assert "FILING, not closure" in content, mirror
