"""Every `trw-distill …` command trw-mcp advertises must actually exist.

`trw_entity_risk_map` returned `trw-distill self-improve entity-risk-map --repo
. --persist-sidecar` as its remediation on every `sidecar_missing` /
`sidecar_malformed` response. That subcommand has never been registered:
enumerating `@self_improve_group.command("…")` across trw-distill's CLI yields
`before-edit` and `risk-report` — the producers behind `trw_before_edit_hint`
and `trw_codebase_risk_report` — and nothing for entity-risk-map.
`DEFECT-LEDGER.md` UF-011 records the same absence from the producer side.

That made it a sharper failure than the unlicensed-artifact defect fixed in
`ed15310966`. There, a *licensed* user's command worked and only an unlicensed
one hit `command not found`. Here the advice could not succeed at any tier, for
anyone — a fabricated capability rather than a gating gap.

`test_entity_risk_map_advertises_nothing_while_uf011_is_open` lived here until
2026-07-29, asserting `_producer_command() is None`. UF-011 was then resolved by
REMOVING the tool, so its subject is gone entirely — the honest end state, since
a tool that cannot advertise anything is strictly better than one that advertises
nothing. The structural check below still covers the general case.

The structural test below is the point of this file. A test asserting
"entity_risk_map no longer returns that one string" would pass while a fifth
tool advertised a different non-existent command tomorrow. This instead derives
BOTH sides — every command trw-mcp advertises, and every command trw-distill
registers — and requires the first to be a subset of the second.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import trw_mcp

#: trw-distill is proprietary and is deliberately absent from trw-mcp's
#: dependencies, so this can only run in the monorepo. That is the right place:
#: the monorepo is where the drift is introduced.
_DISTILL_CLI = Path(trw_mcp.__file__).parents[3] / "trw-distill" / "trw_distill" / "cli"

#: `trw-distill <group> <subcommand>` as it appears inside a Python string.
_ADVERTISED = re.compile(r"trw-distill\s+self-improve\s+([a-z0-9][a-z0-9-]*)")

#: How trw-distill registers a self-improve subcommand.
_REGISTERED = re.compile(r'@self_improve_group\.command\(\s*"([a-z0-9][a-z0-9-]*)"')


def _advertised_commands() -> dict[str, set[str]]:
    """Map each advertising file to the self-improve subcommands it names.

    Scans only NON-DOCSTRING string literals, via the AST. A plain regex over
    file text flagged this test's own explanatory prose — including the
    docstring in `_sidecar_substrate.py` describing the very defect — which is
    the same false-positive class that made a first draft of the entitlement
    guard trip on `"TRW managed: trw-distill PostToolUse telemetry"`. Naming a
    command while explaining it is not advertising it; only a string the code
    can hand to a user is.
    """
    root = Path(trw_mcp.__file__).parent
    found: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        if "self-improve" not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:  # pragma: no cover - defensive
            continue
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    docstrings.add(doc)
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value not in docstrings:
                names |= set(_ADVERTISED.findall(node.value))
        if names:
            # Merge, never assign: two files can share a basename, and an
            # assignment would silently drop one side's advertised commands.
            found.setdefault(path.name, set()).update(names)
    return found


#: The single remaining verified-dormant mention, exempt with a reason rather
#: than pattern-suppressed.
#:
#: `wiring/registry.py` records `entity-risk-map` as the wiring gate's own
#: description of the missing producer (UF-011). Describing an absent producer
#: is the gate doing its job.
#:
#: Three `mdc-emit` exemptions used to sit here for the cursor renderers, with a
#: comment predicting FR01 would delete those files "and when it lands these
#: entries go with them". FR01 landed; the files went; the exemptions did not.
#: They had become latent suppressions — and because the map is keyed on
#: BASENAME, any future file with one of those names would have been exempted
#: silently. Removed 2026-07-28 after an adversarial audit caught them.
_DORMANT: dict[str, set[str]] = {
    "registry.py": {"entity-risk-map"},
}


@pytest.mark.skipif(
    not _DISTILL_CLI.is_dir(),
    reason="trw-distill source not present; this check is monorepo-only",
)
def test_every_advertised_self_improve_command_is_registered() -> None:
    """The subset check. This is what would have caught UF-011 at authoring time."""
    registered: set[str] = set()
    for path in _DISTILL_CLI.rglob("*.py"):
        registered.update(_REGISTERED.findall(path.read_text(encoding="utf-8")))

    assert registered, (
        "no @self_improve_group.command registrations found — the producer-side "
        "regex has rotted, so this test would pass vacuously"
    )

    advertised = _advertised_commands()
    assert advertised, (
        "no `trw-distill self-improve <cmd>` strings found in trw_mcp — the consumer-side regex has rotted"
    )

    fabricated = {
        filename: sorted(names - registered - _DORMANT.get(filename, set()))
        for filename, names in advertised.items()
        if names - registered - _DORMANT.get(filename, set())
    }
    assert not fabricated, (
        "these files tell a user to run a trw-distill subcommand that is not "
        f"registered anywhere in trw-distill's CLI: {fabricated}. Registered "
        f"commands are: {sorted(registered)}"
    )


@pytest.mark.skipif(
    not _DISTILL_CLI.is_dir(),
    reason="trw-distill source not present; this check is monorepo-only",
)
def test_the_two_known_good_producers_are_still_advertised() -> None:
    """Guard the other direction: the subset check must not pass by emptiness.

    If someone removed every advertised command, the test above would go green
    while the tools stopped telling licensed users how to generate a sidecar at
    all. These two are real and load-bearing.
    """
    advertised = _advertised_commands()
    all_names = {name for names in advertised.values() for name in names}

    assert "before-edit" in all_names, "trw_before_edit_hint lost its remediation"
    assert "risk-report" in all_names, "trw_codebase_risk_report lost its remediation"


def test_no_shipped_artifact_advertises_a_dead_trw_mcp_subcommand() -> None:
    """Sibling of the trw-distill guard, for our OWN CLI.

    `channels/antigravity/_explorer_subagent.py` wrote
    `regenerate: trw-mcp channel-render --channel ag-02-...` into the provenance
    header of the installed AG-02 explorer subagent file — a permanent
    file in a **licensed** user's repo, since the entitlement gate opens for
    them. PRD-CORE-239 FR01 deleted that subcommand, so following the header
    exits 2 with "invalid choice". The paying caller got the broken advice and
    nobody else, which is the same asymmetry the entity-risk-map fix names.

    Nothing reads `ChannelEntry.regenerate_cmd`, so no behavioural test could
    catch it. This derives both sides instead: every `trw-mcp <sub>` string the
    package emits, against the subcommands argparse actually registers.
    """
    import re
    from pathlib import Path

    import trw_mcp
    from trw_mcp.server._cli_argparse import _build_arg_parser

    parser = _build_arg_parser()
    registered: set[str] = set()
    if parser._subparsers is not None:
        for action in parser._subparsers._group_actions:
            registered.update(action.choices)
    assert registered, "no subcommands found — the parser introspection has rotted"

    # Invocation context, not adjacency. A first version matched any hyphenated
    # word after "trw-mcp" and flagged three English phrases — "trw-mcp
    # user-facing", "trw-mcp version-drift", "trw-mcp trw-memory". Requiring a
    # following flag or a closing backtick keeps the real shape (the offender
    # was `trw-mcp channel-render --channel ag-02-...`) and drops prose. Same
    # imperative-context discipline as the trw-distill guard above.
    advertised = re.compile(r"trw-mcp\s+([a-z][a-z0-9-]{2,})(?=\s+--|\s*`|\s*$)", re.MULTILINE)
    root = Path(trw_mcp.__file__).parent
    offenders: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in {".py", ".yaml", ".yml", ".md", ".sh"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        bad = sorted({n for n in advertised.findall(text) if n not in registered and "-" in n})
        if bad:
            offenders[str(path.relative_to(root))] = bad

    assert not offenders, (
        "these shipped files name a `trw-mcp` subcommand argparse does not "
        f"register, so following them exits 2: {offenders}. Registered: {sorted(registered)}"
    )

    # Anti-vacuity: fourteen files scanned and nothing found could equally mean
    # the predicate matches nothing. Plant the exact string that shipped.
    planted = sorted(
        {n for n in advertised.findall("regenerate: trw-mcp channel-render --channel ag-02") if n not in registered}
    )
    assert planted == ["channel-render"], f"the predicate cannot see the defect it was written for; got {planted}"
