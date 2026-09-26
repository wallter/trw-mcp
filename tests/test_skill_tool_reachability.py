"""A bundled skill may not instruct an agent to call a tool it cannot reach.

PRD-CORE-281-FR03. The 2026-09-17 report was one instance of a class: the
``trw-delegate`` skill told the agent to call ``trw_dispatch`` first, and no
default session lists that tool — the ``dispatch`` pack is flag-gated. The
agent's own ToolSearch returned no match, so from inside the session the tool
did not appear to exist at all; nothing in the skill said which config flag
turns it on.

PRD-CORE-300 S11b flattened the surface: there is no per-task resolution, no
grant tool, and no discoverable tier. A session sees exactly
``surface_packs.ALWAYS_ON_TOOLS`` plus whichever ``FLAG_GATED_PACKS`` packs
their flag turns on. The guard is retargeted to that flat model: for every
bundled ``SKILL.md``, every ``trw_*(`` CALL SITE must name a tool that is
either

  * in ``ALWAYS_ON_TOOLS`` (exposed in every session, whatever the flags), or
  * in a flag-gated pack, with the skill naming that pack's config flag
    literally (e.g. ``dispatch_tools_exposed``, ``comms_enabled``,
    ``assess_enabled``) — the way through the gate is now a config flag, not a
    grant tool.

``_GRANDFATHERED`` records the offenders that predate the guard, each with the
pack that gates it. It is a RATCHET: the test fails if an entry is stale, so
the list can only shrink. Prose mentions are deliberately not matched — only
the call form ``trw_name(``, which is an instruction to invoke.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from trw_mcp.models.surface_packs import ALWAYS_ON_TOOLS, FLAG_GATED_PACKS, PACK_TOOLS

pytestmark = pytest.mark.unit

_SKILLS_DIR = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "skills"

#: An instruction to invoke, not a prose mention: the name immediately followed
#: by an opening parenthesis.
_CALL_SITE_RE = re.compile(r"\btrw_\w+(?=\()")

#: pack -> config flag, restated from FLAG_GATED_PACKS for the docstring's sake
#: (naming ONE of these literally in the skill counts as documenting the gate).
_GATE_FLAGS: frozenset[str] = frozenset(FLAG_GATED_PACKS.values())

#: skill name -> tool ids whose gate the skill does NOT yet document. Each is a
#: real instance of the reported defect, left visible rather than suppressed.
#: Shrink this; never grow it.
_GRANDFATHERED: dict[str, frozenset[str]] = {
    # peer_comms pack — comms_enabled defaults to True in production (so the
    # tool IS reachable in practice), but the skill never names the flag
    # literally, so the flat-surface guard (which only credits a literal flag
    # mention) still flags it. Recorded rather than suppressed.
    "trw-plan-review": frozenset({"trw_send", "trw_inbox"}),
}


def _pack_for(tool: str) -> str | None:
    for pack, tools in PACK_TOOLS.items():
        if tool in tools:
            return pack
    return None


def _unreachable_calls(path: Path) -> set[str]:
    """Tools this skill tells the agent to CALL that it cannot reach by default."""
    content = path.read_text(encoding="utf-8")
    called = _called_tools(path)
    unreachable: set[str] = set()
    for tool in called:
        if tool in ALWAYS_ON_TOOLS:
            continue
        pack = _pack_for(tool)
        flag = FLAG_GATED_PACKS.get(pack or "")
        if flag and flag in content:
            continue
        unreachable.add(tool)
    return unreachable


def _called_tools(path: Path) -> set[str]:
    return set(_CALL_SITE_RE.findall(path.read_text(encoding="utf-8")))


def _skill_files() -> list[Path]:
    return sorted(_SKILLS_DIR.glob("*/SKILL.md"))


def test_the_scan_finds_call_sites_at_all() -> None:
    """Non-vacuity: a regex that matched nothing would make this file green
    forever whatever the skills say.

    Asserted against a NAMED skill's own text rather than a population count, so
    it neither hardcodes a census nor passes on an empty scan.
    """
    assert _skill_files()
    assert _called_tools(_SKILLS_DIR / "trw-delegate" / "SKILL.md") == {"trw_dispatch"}
    assert "trw_dispatch" not in ALWAYS_ON_TOOLS  # the gate under test still exists


@pytest.mark.parametrize("skill_md", _skill_files(), ids=lambda p: p.parent.name)
def test_every_instructed_tool_is_exposed_or_its_gate_is_documented(skill_md: Path) -> None:
    unreachable = _unreachable_calls(skill_md)
    allowed = _GRANDFATHERED.get(skill_md.parent.name, frozenset())
    offenders = sorted(unreachable - allowed)
    assert not offenders, (
        f"{skill_md.parent.name} instructs the agent to call {offenders}, which no default "
        f"session lists (pack(s): { {t: _pack_for(t) for t in offenders} }). Either expose the "
        f"tool (add it to an always-on pack), or name its config flag in the skill "
        f"(one of {sorted(_GATE_FLAGS)})."
    )


def test_the_grandfather_list_may_only_shrink() -> None:
    """A stale entry is a silent widening of the exemption, so it fails too."""
    stale: list[str] = []
    for name, tools in _GRANDFATHERED.items():
        path = _SKILLS_DIR / name / "SKILL.md"
        if not path.is_file():
            stale.append(f"{name}: skill no longer exists")
            continue
        still_unreachable = _unreachable_calls(path)
        gone = sorted(tools - still_unreachable)
        if gone:
            stale.append(f"{name}: {gone} no longer needs the exemption — remove it")
    assert not stale, "stale grandfather entries:\n  " + "\n  ".join(stale)


def test_the_delegate_skill_is_no_longer_grandfathered() -> None:
    """The reported defect, pinned by name: trw-delegate must pass on its own."""
    assert "trw-delegate" not in _GRANDFATHERED
    assert _unreachable_calls(_SKILLS_DIR / "trw-delegate" / "SKILL.md") == set()


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("comms_enabled: true\ntrw_send()", set()),
        ("dispatch_tools_exposed: true\ntrw_dispatch()", set()),
        ("comms_enabled: true\ntrw_dispatch()", {"trw_dispatch"}),
        ("dispatch_tools_exposed: true\ntrw_send()", {"trw_send"}),
        ("assess_enabled: true\ntrw_assess()", set()),
        ("trw_nonexistent_tool()", {"trw_nonexistent_tool"}),
        ("trw_session_start()", set()),  # kernel: always reachable, no flag needed
        ("trw_dispatch()", {"trw_dispatch"}),  # no flag named at all -> unreachable
    ],
)
def test_gate_documentation_is_specific_and_unknown_calls_fail_closed(
    tmp_path: Path, content: str, expected: set[str]
) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text(content, encoding="utf-8")
    assert _unreachable_calls(skill) == expected
