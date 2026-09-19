"""A bundled skill may not instruct an agent to call a tool it cannot reach.

PRD-CORE-281-FR03. The 2026-09-17 report was one instance of a class: the
``trw-delegate`` skill told the agent to call ``trw_dispatch`` first, and no
default session lists that tool — the ``dispatch`` pack is named by no entry of
``STANDARD_TASK_PACKS``. The agent's own ToolSearch returned no match, so from
inside the session the tool did not appear to exist at all; nothing in the skill
said the word ``trw_request_tool_access``.

The guard: for every bundled ``SKILL.md``, every ``trw_*(`` CALL SITE must name
a tool that is either

  * in the default-exposed baseline — the union over every task type of
    ``resolve_tool_surface(task, "standard")`` plus the middleware's never-hide
    set (this is what "exposed by default" means in production), or
  * accompanied, IN THE SAME SKILL, by the way through the gate
    (``trw_request_tool_access`` or the named config opt-in).

``_GRANDFATHERED`` records the offenders that predate the guard, each with the
pack that gates it. It is a RATCHET: the test fails if an entry is stale, so the
list can only shrink. Prose mentions are deliberately not matched — only the
call form ``trw_name(``, which is an instruction to invoke.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from trw_mcp.middleware.surface_authority import _ALWAYS_EXPOSED
from trw_mcp.models.surface_packs import PACK_TOOLS, STANDARD_TASK_PACKS
from trw_mcp.server._surface_manifest_registry import resolve_tool_surface
from trw_mcp.state.claude_md._tool_manifest import TOOL_DESCRIPTIONS

pytestmark = pytest.mark.unit

_SKILLS_DIR = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "skills"

#: An instruction to invoke, not a prose mention: the name immediately followed
#: by an opening parenthesis.
_CALL_SITE_RE = re.compile(r"\btrw_\w+(?=\()")

#: Phrases that constitute "documented access request" for this guard.
_CONFIG_GATES = {
    "dispatch_tools_exposed": {"dispatch_enabled": True},
    "comms_enabled": {"comms_enabled": True},
}
_GATE_MARKERS = ("trw_request_tool_access", *_CONFIG_GATES)

#: skill name -> tool ids whose gate the skill does NOT yet document. Each is a
#: real instance of the reported defect, left visible rather than suppressed.
#: Shrink this; never grow it.
_GRANDFATHERED: dict[str, frozenset[str]] = {
    # code_risk pack — an index refresh the skill tells the agent to run first.
    "trw-code-search": frozenset({"trw_code_index_update"}),
    # peer_comms pack — additionally gated by default-off comms_enabled.
    "trw-plan-review": frozenset({"trw_send", "trw_inbox"}),
    # telemetry_security pack — the same class recorded in learning L-R15h
    # (session advisories pointing at tools the session cannot call).
    "trw-project-health": frozenset({"trw_mcp_security_status", "trw_pipeline_health"}),
}


def _default_exposed() -> frozenset[str]:
    """What a session can list WITHOUT an operator opt-in or a grant."""
    surface = set(_ALWAYS_EXPOSED)
    for task_type in (None, *STANDARD_TASK_PACKS):
        surface |= set(resolve_tool_surface(task_type, "standard").tools)
    return frozenset(surface)


def _skill_files() -> list[Path]:
    return sorted(_SKILLS_DIR.glob("*/SKILL.md"))


def _unreachable_calls(path: Path, baseline: frozenset[str]) -> set[str]:
    """Tools this skill tells the agent to CALL that it cannot reach by default."""
    content = path.read_text(encoding="utf-8")
    gated = _called_tools(path) - baseline
    # A grant cannot make an unknown/misspelled tool exist.
    if "trw_request_tool_access" in content:
        gated -= set(TOOL_DESCRIPTIONS)
    for marker, opt_in in _CONFIG_GATES.items():
        if marker in content:
            # Derive each opt-in's tool set from the production resolver rather
            # than treating an unrelated config mention as access to all packs.
            exposed = resolve_tool_surface(None, "standard", **opt_in).tools
            default = resolve_tool_surface(None, "standard").tools
            gated -= set(exposed) - set(default)
    return gated


def _called_tools(path: Path) -> set[str]:
    return set(_CALL_SITE_RE.findall(path.read_text(encoding="utf-8")))


def test_the_scan_finds_call_sites_at_all() -> None:
    """Non-vacuity: a regex that matched nothing would make this file green
    forever whatever the skills say.

    Asserted against a NAMED skill's own text rather than a population count, so
    it neither hardcodes a census nor passes on an empty scan.
    """
    assert _skill_files()
    assert _called_tools(_SKILLS_DIR / "trw-delegate" / "SKILL.md") == {
        "trw_dispatch",
        "trw_dispatch_status",
        "trw_request_tool_access",  # the gate the skill now names — itself a call site
    }
    assert "trw_dispatch" not in _default_exposed()  # the gate under test still exists


@pytest.mark.parametrize("skill_md", _skill_files(), ids=lambda p: p.parent.name)
def test_every_instructed_tool_is_exposed_or_its_gate_is_documented(skill_md: Path) -> None:
    baseline = _default_exposed()
    unreachable = _unreachable_calls(skill_md, baseline)
    allowed = _GRANDFATHERED.get(skill_md.parent.name, frozenset())
    offenders = sorted(unreachable - allowed)
    assert not offenders, (
        f"{skill_md.parent.name} instructs the agent to call {offenders}, which no default "
        f"session lists (pack(s): "
        f"{ {t: [p for p, ts in PACK_TOOLS.items() if t in ts] for t in offenders} }). "
        "Either expose the tool, or say in the skill how to reach it "
        f"(one of {list(_GATE_MARKERS)})."
    )


def test_the_grandfather_list_may_only_shrink() -> None:
    """A stale entry is a silent widening of the exemption, so it fails too."""
    baseline = _default_exposed()
    stale: list[str] = []
    for name, tools in _GRANDFATHERED.items():
        path = _SKILLS_DIR / name / "SKILL.md"
        if not path.is_file():
            stale.append(f"{name}: skill no longer exists")
            continue
        still_unreachable = _unreachable_calls(path, baseline)
        gone = sorted(tools - still_unreachable)
        if gone:
            stale.append(f"{name}: {gone} no longer needs the exemption — remove it")
    assert not stale, "stale grandfather entries:\n  " + "\n  ".join(stale)


def test_the_delegate_skill_is_no_longer_grandfathered() -> None:
    """The reported defect, pinned by name: trw-delegate must pass on its own."""
    assert "trw-delegate" not in _GRANDFATHERED
    assert _unreachable_calls(_SKILLS_DIR / "trw-delegate" / "SKILL.md", _default_exposed()) == set()


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("comms_enabled: true\ntrw_dispatch()", {"trw_dispatch"}),
        ("dispatch_tools_exposed: true\ntrw_send()", {"trw_send"}),
        ("comms_enabled: true\ntrw_send()", set()),
        ("dispatch_tools_exposed: true\ntrw_dispatch()", set()),
        ("trw_nonexistent_tool()", {"trw_nonexistent_tool"}),
        (
            "trw_request_tool_access()\ntrw_nonexistent_tool()",
            {"trw_nonexistent_tool"},
        ),
        ("trw_request_tool_access()\ntrw_dispatch()", set()),
        ("comms_enabled: true\ntrw_nonexistent_tool()", {"trw_nonexistent_tool"}),
    ],
)
def test_gate_documentation_is_specific_and_unknown_calls_fail_closed(
    tmp_path: Path, content: str, expected: set[str]
) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text(content, encoding="utf-8")
    assert _unreachable_calls(skill, _default_exposed()) == expected
