"""PRD-SEC-015 FR01/FR09/FR15 — the reviewer tool list exists exactly once.

FR01 pins the ``REVIEWER_TOOLS`` frozenset itself: registered read-report
tools, disjoint from every write / verdict / dispatch / sync / escalation tool.
PRD-CORE-300 slice S4 dropped the codebase-risk-report tool from the set
(moved to the ``trw-mcp code risk`` CLI) and S10 folded four code-navigation
tools into ``trw_code``, so the member count is measured from
``len(REVIEWER_TOOLS)`` rather than hardcoded in prose.
FR09 pins the ONE generator (``scripts/print_reviewer_tools.py``) that renders it
for the shell audit lane, and FR15 pins the agy branch of that lane.

US-004: a second literal list is the defect. The membership assertions here are
derived from :mod:`trw_mcp.models.surface_packs` and the live manifest registry,
never from a hand-typed copy.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import tomllib

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT, requires_monorepo
from trw_mcp.models.surface_packs import (
    CAPABILITY_PACKS,
    REVIEWER_TOOLS,
    reviewer_tools_toml_array,
)
from trw_mcp.server._surface_manifest_registry import eligible_tool_names

_REPO = MONOREPO_ROOT or PACKAGE_ROOT.parent
_AUDIT_SCRIPT = _REPO / "scripts/audit-external.sh"


def _script_code() -> str:
    """``scripts/audit-external.sh`` with comment-only lines dropped.

    Flag assertions have to read the CODE: a `grep_absent` over the whole file
    would forbid the comment that records WHY the flag is absent, which is the
    measurement a future maintainer needs most.
    """
    return "\n".join(
        line for line in _AUDIT_SCRIPT.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
    )


#: Tools that mutate the shared learnings store, run state, pins, ceremony
#: state, instruction files, PRD files, or a gate verdict (PRD-SEC-015 §FR01
#: exclusion table). Hand-enumerated on purpose: this is the independent
#: statement the disjointness proof is measured against, so deriving it from the
#: same table it checks would make the assertion vacuous.
_WRITE_CLASS: frozenset[str] = frozenset(
    {
        "trw_session_start",
        "trw_status",
        # PRD-CORE-291 merged trw_learn_update into trw_learn's update mode
        # (learning_id set); it is no longer a separate registered tool.
        "trw_learn",
        "trw_checkpoint",
        "trw_init",
        "trw_build_check",
        "trw_review",
        "trw_deliver",
        "trw_prd_validate",
        # PRD-CORE-300 S6a folded the former heartbeat and pre-compact-checkpoint tools
        # into trw_checkpoint's heartbeat=True / pre_compact=True modes; they
        # are no longer separate registered tools.
    }
)

#: Recursion class — a reviewer spawning reviewers (derived: the whole pack).
_DISPATCH_CLASS: frozenset[str] = frozenset(CAPABILITY_PACKS["dispatch"])

#: The tool that once truncated 128 lines / 9 sections of hand-written rules out
#: of AGENTS.md on 2026-07-23 was folded into `trw-mcp instructions sync`
#: (PRD-CORE-300 S6b) — a CLI verb, not a registered MCP tool, so it cannot be
#: a member of this frozenset (the non-vacuity check below requires every
#: member to be a live registered tool). Kept as an empty set, not deleted, so
#: a future sync-shaped tool has somewhere to land without re-deriving the class.
_SYNC_CLASS: frozenset[str] = frozenset()

# PRD-CORE-300 S11b deleted the grant/escalation tool
# outright — there is no self-escalation primitive left to name, so there is no
# ``_ESCALATION_CLASS`` any more. The bound (US-002) now holds structurally: a
# tool outside REVIEWER_TOOLS is reached only by turning on its config flag,
# which the reviewer role never consults.


def test_reviewer_tools_disjoint_from_write_and_dispatch_and_sync_sets() -> None:
    """FR01: the reviewer surface intersects no write/dispatch/sync tool."""
    forbidden = _WRITE_CLASS | _DISPATCH_CLASS | _SYNC_CLASS

    assert REVIEWER_TOOLS & forbidden == frozenset()
    # Non-vacuity: the forbidden set is not empty and names live tools.
    assert forbidden <= set(eligible_tool_names())
    assert "trw_deliver" in forbidden


def test_reviewer_tools_are_exactly_the_two_post_cut_read_report_tools() -> None:
    """FR01 / PRD-CORE-300-NFR02: two members, ``trw_recall`` and ``trw_code``.
    S4 dropped the codebase-risk-report tool, S9 folded the graph-related tool
    into trw_recall's graph mode, S10 swapped the four code-navigation tools for
    ``trw_code``, and S11b dropped the two meta tools (skill discovery, profile
    explain). Both are registered public tool ids."""
    assert isinstance(REVIEWER_TOOLS, frozenset)
    assert REVIEWER_TOOLS == frozenset({"trw_recall", "trw_code"})
    assert REVIEWER_TOOLS <= set(eligible_tool_names())


def test_reviewer_tools_toml_array_round_trips_to_the_sorted_ssot() -> None:
    """FR01/FR09: the formatter is the ONE rendering of the SSOT."""
    parsed = tomllib.loads(f"enabled_tools = {reviewer_tools_toml_array()}")

    assert parsed["enabled_tools"] == sorted(REVIEWER_TOOLS)


#: One flat collection literal: no nested brackets inside it.
_COLLECTION_LITERAL = re.compile(r"[\[{(][^\[\]{}()]*[\]})]")


def test_no_hand_typed_reviewer_list_exists_outside_the_ssot() -> None:
    """US-004: only ``models/surface_packs.py`` may enumerate the reviewer set.

    A second copy is REVIEWER-SHAPED: one collection literal (``[...]``,
    ``{...}`` or ``(...)``) that names every member as a quoted string while
    naming at most one excluded write/dispatch/sync tool. PRD-CORE-300 shrank the
    set to two common names (``trw_recall``, ``trw_code``), so a whole-file
    count would flag every file that merely mentions both; the literal is the
    shape a drifting duplicate has. Literals enumerating the FULL registered
    surface name every write tool too, so they are not copies of this set.
    """
    ssot = PACKAGE_ROOT / "src/trw_mcp/models/surface_packs.py"
    # The PRD-CORE-300 end-state spec states the target set on purpose;
    # tests/test_kernel_is_post_cut_kernel.py holds the SSOT equal to it.
    spec = PACKAGE_ROOT / "src/trw_mcp/models/surface_v2.py"
    excluded = _WRITE_CLASS | _DISPATCH_CLASS | _SYNC_CLASS
    roots = [PACKAGE_ROOT / "src/trw_mcp"]
    if MONOREPO_ROOT is not None:
        roots.append(MONOREPO_ROOT / "scripts")
    offenders: list[tuple[str, int, int]] = []
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".sh"} or path in (ssot, spec):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for literal in _COLLECTION_LITERAL.findall(text):
                hits = sum(1 for name in REVIEWER_TOOLS if f'"{name}"' in literal)
                write_hits = sum(1 for name in excluded if f'"{name}"' in literal)
                if hits >= len(REVIEWER_TOOLS) and write_hits <= 1:
                    offenders.append((str(path.relative_to(_REPO)), hits, write_hits))

    assert offenders == [], f"hand-typed reviewer tool list(s): {offenders}"
    # Non-vacuity: the SSOT itself has exactly the reviewer shape this detects
    # (every member, and no write tool inside the frozenset literal).
    ssot_text = ssot.read_text(encoding="utf-8")
    literal = ssot_text.split("REVIEWER_TOOLS: frozenset[str] = frozenset(", 1)[1].split(")", 1)[0]
    assert sum(1 for name in REVIEWER_TOOLS if name in literal) == len(REVIEWER_TOOLS)
    assert sum(1 for name in excluded if name in literal) == 0
    # ...and the detector's literal pattern does find it there.
    assert any(all(f'"{name}"' in found for name in REVIEWER_TOOLS) for found in _COLLECTION_LITERAL.findall(ssot_text))


# ── FR09: the shell audit lane reads the SSOT through one generator ────


@requires_monorepo
def test_generator_output_matches_ssot_and_script_uses_it() -> None:
    """FR09: generator stdout is the TOML array, and the codex lane invokes it."""
    proc = subprocess.run(
        [sys.executable, str(_REPO / "scripts/print_reviewer_tools.py")],
        capture_output=True,
        text=True,
        cwd=str(_REPO),
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.count("\n") == 1, f"stdout must be one line and nothing else: {proc.stdout!r}"
    assert tomllib.loads(f"enabled_tools = {proc.stdout.strip()}")["enabled_tools"] == sorted(REVIEWER_TOOLS)

    script = _script_code()
    assert "print_reviewer_tools.py" in script
    assert 'mcp_servers.trw.env.TRW_SURFACE_ROLE="reviewer"' in script
    assert "mcp_servers.trw.enabled_tools=" in script
    # Probes 2/3 (codex-cli 0.153.2): --ignore-user-config drops the project trw
    # server outright and the -c overrides cannot coexist with it. The allowlist
    # layer DEPENDS on the project config supplying the MCP transport. Asserted
    # against the CODE only, so the reasoning may name the flag in a comment.
    assert "--ignore-user-config" not in script


@requires_monorepo
def test_generator_does_not_depend_on_an_installed_trw_mcp_on_PATH() -> None:
    """FR09: the generator renders THIS repository's set, not an installed one.

    ``python3`` on a developer box can resolve to a foreign virtualenv holding a
    released ``trw_mcp``; rendering that package's (older, or absent) set into
    the ``-c`` allowlist would bound the child against a list this repository
    never reviewed. ``-S -E`` makes any installed ``trw_mcp`` unimportable, so a
    pass here proves the SSOT is read from the repository tree.
    """
    proc = subprocess.run(
        [sys.executable, "-S", "-E", str(_REPO / "scripts/print_reviewer_tools.py")],
        capture_output=True,
        text=True,
        cwd="/",
    )

    assert proc.returncode == 0, proc.stderr
    assert tomllib.loads(f"enabled_tools = {proc.stdout.strip()}")["enabled_tools"] == sorted(REVIEWER_TOOLS)


@requires_monorepo
def test_generator_exits_nonzero_when_the_ssot_is_unreachable(tmp_path: Path) -> None:
    """FR09 negative: a broken SSOT read must NOT emit an empty array.

    An empty ``enabled_tools=[]`` substituted into the ``-c`` flag would leave the
    child unbounded rather than bounded, so the failure has to be loud. The copy
    below sits outside the repository, so its SSOT lookup cannot resolve.
    """
    stray = tmp_path / "print_reviewer_tools.py"
    stray.write_text((_REPO / "scripts/print_reviewer_tools.py").read_text(encoding="utf-8"), encoding="utf-8")

    proc = subprocess.run([sys.executable, str(stray)], capture_output=True, text=True, cwd=str(tmp_path))

    assert proc.returncode != 0
    assert "[" not in proc.stdout
    assert "surface_packs" in proc.stderr


@requires_monorepo
def test_audit_external_codex_lane_fails_closed_on_a_generator_failure() -> None:
    """FR09: the generated array is bound to a variable BEFORE the codex call.

    ``set -e`` aborts on a failing command substitution in a plain assignment,
    but NOT on one spliced directly into another command's arguments — inline
    substitution would pass an empty allowlist to codex and widen the child on
    exactly the failure the generator's non-zero exit exists to signal.
    """
    script = _script_code()
    assign = [ln for ln in script.splitlines() if "print_reviewer_tools.py" in ln]

    assert len(assign) == 1, assign
    assert assign[0].strip().startswith("reviewer_tools="), assign
    assert "enabled_tools=$reviewer_tools" in script or "enabled_tools=${reviewer_tools}" in script


# ── FR15: the agy branch ships exactly one measured posture ────────────


@requires_monorepo
def test_audit_external_agy_branch_is_read_only() -> None:
    """FR15: exactly ONE of the two branches holds, with its required evidence.

    Branch A (probe passed): ``--sandbox`` plus a read-only ``permissions.allow``
    allowlist, no ``--dangerously-skip-permissions``.
    Branch B (probe failed, the binding fallback): the bypass flag REMAINS and the
    measurement that justifies it is written into the script header.

    Measured 2026-09-04 against agy 1.1.26 (transcript: the SEC-015 run's
    ``reports/fr15-agy-repo-scoped-permissions-probe.md``): a repo-scoped
    ``.antigravitycli/settings.json`` ``permissions.allow`` is NOT honoured —
    exit 0, empty stdout, and the headless auto-deny notice — so branch B ships.
    """
    code = _script_code()
    full = _AUDIT_SCRIPT.read_text(encoding="utf-8")
    header = "\n".join(full.splitlines()[:60])
    agy_invocation = [ln for ln in code.splitlines() if ln.strip().startswith("agy ")]
    assert len(agy_invocation) == 1, agy_invocation
    agy_line = agy_invocation[0]

    allowlist_branch = "permissions.allow" in code
    bypass_branch = "--dangerously-skip-permissions" in agy_line
    assert allowlist_branch != bypass_branch, "exactly one FR15 branch may ship"

    if allowlist_branch:
        write_verbs = ("rm ", "mv ", "cp ", "tee", "git commit", "git push", "npm ", "pip ", "vim", "sed -i")
        allow_block = code.split("permissions.allow", 1)[1]
        assert not any(verb in allow_block for verb in write_verbs), allow_block
        assert "--dangerously-skip-permissions" not in code
    else:
        # The bypass may only remain WITH the measurement beside the existing
        # `--mode plan` note — a documented limitation, never a bare flag.
        assert "permissions.allow" in header, "the fallback must record what was measured"
        assert "--sandbox" in header
        assert "auto-denied" in header or "auto-denies" in header

    # Both branches carry the reviewer role (FR09), even though agy headless
    # spawns no MCP server to receive it today — it must never be reported as
    # containment, only forwarded.
    assert "TRW_SURFACE_ROLE=reviewer" in code
