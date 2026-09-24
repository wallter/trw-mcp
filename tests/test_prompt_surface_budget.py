"""Prompt-surface (skills + agents) byte-budget tripwire.

PRD-CORE-291-FR06: `test_tool_definition_budget.py` measures the tool
*definition* surface -- descriptions and parameter schemas -- but nothing
measured the skill/agent *prompt* surface the same way until now. Every
skill and agent body is paid unconditionally in whichever client renders
it, exactly like a tool definition, so a regrowth there is the same class
of cost.

This test measures three things independently:

1. The forbidden fork directories (`data/codex/skills`, `data/copilot/skills`,
   `data/opencode/skills`) stay absent -- checked regardless of size, because
   even a tiny fork reintroduces the drift class PRD-CORE-291-FR04 removed.
2. The combined canonical byte size of `data/skills` (every file of every
   skill -- `SKILL.md` plus companions like `audit-framework.md` and
   `PR-TEMPLATE.md`, not `SKILL.md` alone) and `data/agents`.
3. Every client's *shipped* skill-surface bytes and every agent-capable
   client's *rendered* agent bytes, summed with (2) into one grand total.
   "Every client" means every client that actually ships a skill surface --
   claude-code and cursor-ide included, not only the three (codex/copilot/
   opencode) whose skill body is transformed by `render_skill_md`. Skill
   bytes are counted via the same enumerators the installer itself calls
   (`skill_files`/`skill_names` for claude-code and the three renderers,
   `cursor_skill_mirror_contents` for cursor-ide's curated list) -- not a
   second, parallel file list that could drift from what actually ships.
   Agent bytes use `materialize_agent`, as before.

   Measured at BOTH `TRWConfig.assess_enabled` values (default off, and on):
   `trw-assess` is a conditional skill (`CONDITIONAL_SKILLS`) that every
   skill installer (claude-code, codex, copilot, opencode, cursor-ide)
   ships only while the flag is on, so
   the off-only measurement understated what an assess-enabled install
   actually ships. The ceiling is checked against the max of the two.

The grand total is checked against a ceiling fixed at this measurement plus a
small fixed margin, mirroring `test_tool_definition_budget.py`'s tightened
"measurement plus under 100" ceiling pattern (not a proportional percentage,
which would hide real regrowth at this scale). Raising the ceiling in the
same diff that grows the corpus does not satisfy this gate -- a real
reduction or an explicit, separately-justified budget change is required.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Final

import pytest

pytestmark = pytest.mark.unit

# Measurement (2026-09-23, re-measured after batch 17's Jev enablement +
# trw-learn skill fixes, real installer enumerators over the production
# canonical skills/agents corpus -- every shipped file per skill, for every
# client that ships a skill surface, at both assess_enabled values). Ceiling
# = the measured max + a small fixed margin (mirrors
# test_tool_definition_budget.py's FULL_SURFACE_CEILING_CHARS history: "the
# measurement plus under 60/100", not a percentage), so ordinary wording
# edits do not trip the wire but a reintroduced fork or an unreviewed
# regrowth does.
#
# Re-measured 2026-09-23 with no corpus growth: skill_files(client, "trw-prd-ready")
# now returns the <phase>-contract.md files codex and opencode had always written
# there without recording them (the uninstall orphan fix). Codex's folded phases
# are skipped as skills of their own so they count once; opencode's four contracts
# (+44,790 bytes, shipped all along) are now measured. Margin unchanged (+80).
#
# Re-measured 2026-09-23 after the trw-assess skill rewrite (177 -> ~60 lines) and copilot
# joining the clients that ship it: default -5,888, assess_enabled -24,380. Ceiling lowered
# to the new max + the same +80 margin.
_MEASURED_GRAND_TOTAL_BYTES_DEFAULT: Final[int] = 1_359_916
_MEASURED_GRAND_TOTAL_BYTES_ASSESS_ENABLED: Final[int] = 1_382_974
PROMPT_SURFACE_CEILING_BYTES: Final[int] = 1_383_054

_FORK_CLIENTS: Final[tuple[str, ...]] = ("codex", "copilot", "opencode")

#: Every client that ships a skill surface via `skill_names`/`skill_files`
#: (byte-copy for claude-code, rendered for the other three). cursor-ide is
#: handled separately below: it has its own curated skill list
#: (`_IDE_CURATED_SKILLS`) and mirror function, not `skill_names`/`skill_files`.
_SKILL_SHIPPING_CLIENTS: Final[tuple[str, ...]] = ("claude-code", *_FORK_CLIENTS)

#: Clients whose own installer ships a `CONDITIONAL_SKILLS` member once its
#: feature flag is on (verified against each installer directly):
#: `_init_project_skills.py` iterates every skill and gates only on
#: `skill_enabled()` for claude-code; `_codex.py:460` explicitly appends
#: enabled conditional skills; `_opencode.py:225` ships whatever its
#: inventory doesn't mark "exclude" and `skill_enabled()` allows (trw-assess's
#: disposition is "portable"); `copilot_skill_contents` appends enabled
#: conditional skills to copilot's curated set the same way codex does.
_ASSESS_SHIPPING_CLIENTS: Final[tuple[str, ...]] = ("claude-code", "codex", "copilot", "opencode")


def _pkg_data() -> Path:
    from tests._test_bundle_asset_support import _PKG_DATA

    return _PKG_DATA


def _canonical_bytes(pkg_data: Path | None = None) -> tuple[int, int]:
    pkg_data = pkg_data or _pkg_data()
    skill_bytes = sum(len(p.read_bytes()) for p in (pkg_data / "skills").rglob("*") if p.is_file())
    agent_bytes = sum(len(p.read_bytes()) for p in (pkg_data / "agents").glob("*.md"))
    return skill_bytes, agent_bytes


def _rendered_skill_bytes(*, assess_enabled: bool, root: Path | None = None) -> int:
    """Every client's shipped skill-surface bytes, via the installer's own enumerators.

    Uses `skill_files`/`skill_names` (the functions `_install_skills` and the
    codex/copilot/opencode installers actually call) for claude-code and the
    three rendering clients, and `cursor_skill_mirror_contents` (the function
    `generate_cursor_ide_skills` actually calls) for cursor-ide's curated
    subset. Every enumerator counts every file of every shipped skill, not
    `SKILL.md` alone. *root* overrides the canonical skills directory the
    bytes are read from (used by the planted-growth regression test below);
    membership (which names are shipped) is unaffected by *root*, since only
    the byte content -- not which skills exist -- needs to vary for that test.
    """
    from trw_mcp.bootstrap._client_skills import PRD_READY_CONTRACTS, canonical_skills_dir, skill_files, skill_names
    from trw_mcp.bootstrap._cursor import cursor_skill_mirror_contents
    from trw_mcp.bootstrap._cursor_ide import _IDE_CURATED_SKILLS
    from trw_mcp.bootstrap._optional_skills import CONDITIONAL_SKILLS

    skill_root = root or canonical_skills_dir()
    total = 0
    for client in _SKILL_SHIPPING_CLIENTS:
        # Codex ships a folded readiness phase only as trw-prd-ready/<phase>-contract.md.
        names = [n for n in skill_names(client) if client != "codex" or n not in PRD_READY_CONTRACTS["codex"]]
        if assess_enabled and client in _ASSESS_SHIPPING_CLIENTS:
            names.extend(name for name in CONDITIONAL_SKILLS if name not in names)
        for name in names:
            total += sum(len(data) for _filename, data in skill_files(client, name, root=skill_root))

    # cursor-ide has its own curated list and mirror function, independent of
    # skill_names/skill_files.
    cursor_names = [name for name in _IDE_CURATED_SKILLS if assess_enabled or name not in CONDITIONAL_SKILLS]
    cursor_contents = cursor_skill_mirror_contents(cursor_names, skill_root)
    total += sum(len(data) for data in cursor_contents.values())
    return total


def _agent_capable_clients() -> list[str]:
    from trw_mcp.agents.agent_formats import agent_format_for
    from trw_mcp.agents.tier_resolver import KNOWN_CLIENTS

    return sorted(c for c in KNOWN_CLIENTS if agent_format_for(c).unsupported_reason is None)


def _rendered_agent_bytes(pkg_data: Path | None = None) -> int:
    from trw_mcp.agents.tier_resolver import materialize_agent

    pkg_data = pkg_data or _pkg_data()
    total = 0
    for client in _agent_capable_clients():
        for path in sorted((pkg_data / "agents").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            total += len(materialize_agent(text, client=client).encode("utf-8"))
    return total


def _grand_total_bytes(pkg_data: Path | None = None, *, assess_enabled: bool = False) -> int:
    pkg_data = pkg_data or _pkg_data()
    skill_bytes, agent_bytes = _canonical_bytes(pkg_data)
    rendered_skills = _rendered_skill_bytes(assess_enabled=assess_enabled, root=pkg_data / "skills")
    return skill_bytes + agent_bytes + rendered_skills + _rendered_agent_bytes(pkg_data)


def _assert_within_ceiling(total: int) -> None:
    """The one place the ceiling comparison is written.

    Both `test_skills_and_agents_within_ceiling` (the production gate) and
    `test_ceiling_fails_on_planted_growth_regression` (its own proof that the
    gate can fail) call this, so a future change to the comparison cannot
    silently diverge between the real check and the test that proves it works.
    """
    assert total <= PROMPT_SURFACE_CEILING_BYTES, (
        f"Prompt surface (skills+agents, canonical + rendered) is {total} bytes, "
        f"over the {PROMPT_SURFACE_CEILING_BYTES}-byte ceiling "
        f"(measured baseline: default={_MEASURED_GRAND_TOTAL_BYTES_DEFAULT}, "
        f"assess_enabled={_MEASURED_GRAND_TOTAL_BYTES_ASSESS_ENABLED}). Trim the corpus, or raise the "
        "ceiling only with a separately-justified budget change -- never in the same diff "
        "that grows it."
    )


def _fork_offenders(pkg_data: Path) -> list[str]:
    """List of fork clients whose ``skills`` directory exists under *pkg_data*.

    The single definition of "a forbidden skill fork is present" -- both
    ``test_skill_fork_directories_absent`` (the production gate) and
    ``test_ceiling_fails_on_planted_fork_regression`` (its own proof that the
    gate can fail) call this, so a future change to the detection logic
    cannot silently diverge between the real check and the test that proves
    the check works.
    """
    return [client for client in _FORK_CLIENTS if (pkg_data / client / "skills").exists()]


def test_skill_fork_directories_absent() -> None:
    """The three hand-authored skill forks never come back, regardless of size."""
    offenders = _fork_offenders(_pkg_data())
    assert not offenders, (
        f"Forbidden hand-authored skill fork director{'y' if len(offenders) == 1 else 'ies'} present: "
        f"{offenders}. Every client renders its skill surface from data/skills (PRD-CORE-291-FR04)."
    )


def test_skills_and_agents_within_ceiling() -> None:
    """Canonical + rendered skill/agent bytes stay within the post-reduction ceiling.

    Checked at both `TRWConfig.assess_enabled` values -- the max of the two
    is what an operator could actually install.
    """
    default_total = _grand_total_bytes(assess_enabled=False)
    assess_enabled_total = _grand_total_bytes(assess_enabled=True)
    _assert_within_ceiling(max(default_total, assess_enabled_total))


def test_ceiling_fails_on_planted_fork_regression(tmp_path: Path) -> None:
    """A restored fork file trips the ceiling/fork-absence gate in a scratch copy.

    Proves the gate is a real regression detector, not a tautology: copy the
    package data tree, reintroduce one hand-authored fork file, and confirm
    ``_fork_offenders`` -- the exact function ``test_skill_fork_directories_absent``
    calls, not a parallel reimplementation -- detects it against the scratch
    copy. A future change to the detection logic cannot silently stop
    catching regressions while this test keeps passing, because both tests
    now call the same code.
    """
    pkg_data = _pkg_data()
    scratch_data = tmp_path / "data"
    shutil.copytree(pkg_data, scratch_data)

    fork_dir = scratch_data / "codex" / "skills" / "trw-planted-regression"
    fork_dir.mkdir(parents=True)
    (fork_dir / "SKILL.md").write_text(
        "---\nname: trw-planted-regression\ndescription: test-only\n---\nbody\n",
        encoding="utf-8",
    )

    offenders = _fork_offenders(scratch_data)
    assert offenders == ["codex"], (
        "Planted fork directory was not detected by the same check "
        "test_skill_fork_directories_absent uses -- the gate would not catch a real regression."
    )


def test_ceiling_fails_on_planted_growth_regression(tmp_path: Path) -> None:
    """Growing a shipped skill file trips the SAME production ceiling assertion.

    Proves ``_assert_within_ceiling`` -- the exact function
    ``test_skills_and_agents_within_ceiling`` calls -- is a real regression
    detector, not a tautology that always passes: copy the package data
    tree, append enough bytes to one shipped skill's companion file
    (``trw-audit/audit-framework.md``, shipped by every skill-shipping
    client) to push the scratch corpus's grand total past the ceiling, and
    confirm the shared assertion helper raises.
    """
    pkg_data = _pkg_data()
    scratch_data = tmp_path / "data"
    shutil.copytree(pkg_data, scratch_data)

    grown_file = scratch_data / "skills" / "trw-audit" / "audit-framework.md"
    with grown_file.open("a", encoding="utf-8") as handle:
        handle.write("x" * 200_000)

    grown_total = _grand_total_bytes(scratch_data)
    assert grown_total > PROMPT_SURFACE_CEILING_BYTES, (
        "Planted growth did not exceed the ceiling -- the scratch corpus grew, but the total "
        f"({grown_total}) still fits under {PROMPT_SURFACE_CEILING_BYTES}; increase the planted size."
    )
    with pytest.raises(AssertionError):
        _assert_within_ceiling(grown_total)
