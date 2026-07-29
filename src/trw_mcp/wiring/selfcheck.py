"""FR08 — the detector asserts its own invocation.

This PRD's own failure mode is becoming the next specimen. The precedent is
exact: ``docs/documentation/specs/dead-code-audit.json`` was generated on
2026-03-29 with 22 findings including 3 P0s, was indexed as a spec artifact,
**never ran again**, and 117 days later at least seven of its findings were
still open — including a P0 on a published PyPI package's public constructor.
Detection was never the missing capability. Recurrence was.

So the detector registers itself as a contract and checks three things about the
``Makefile`` on every run:

1. its target is a prerequisite of ``check`` — deleting the line fails the run;
2. its recipe actually invokes ``trw_mcp.wiring.cli`` — a target that exists but
   runs something else is the same defect in a nicer costume;
3. its recipe does not pass ``--advisory`` — the escape hatch exists for a human
   at a terminal, and wiring it into CI would silently convert this gate back
   into a report, which is the outcome the whole PRD is built against. Checks 2
   and 3 match against the shell-normalized recipe (see ``_shell_normalized``),
   because a recipe is shell source and quoting can hide a flag from a literal
   substring search while still passing it to the CLI.
"""

from __future__ import annotations

import re
from pathlib import Path

from trw_mcp.wiring.model import EdgeClass, Finding
from trw_mcp.wiring.registry import ArtifactContract

CLI_MODULE = "trw_mcp.wiring.cli"
ADVISORY_FLAG = "--advisory"


def _prerequisites_of(makefile_text: str, target: str) -> list[str]:
    pattern = re.compile(rf"^{re.escape(target)}:(?!=)([^\n]*)$", re.MULTILINE)
    match = pattern.search(makefile_text)
    if match is None:
        return []
    body = match.group(1).split("##", 1)[0]
    return body.split()


def _recipe_of(makefile_text: str, target: str) -> str:
    pattern = re.compile(rf"^{re.escape(target)}:(?!=)[^\n]*\n((?:\t[^\n]*\n|\n)*)", re.MULTILINE)
    match = pattern.search(makefile_text)
    return match.group(1) if match else ""


# A recipe is shell source, not prose, so matching flags against its raw text is
# the substring-matching defect this repo keeps rediscovering
# (``.claude/rules/trw-mcp-python.md`` "Marker / Sentinel Matching"). ``--advi""sory``
# and ``--advi\`` + newline + ``sory`` both reach the CLI as ``--advisory`` while the
# literal flag never appears in the Makefile text — a one-keystroke bypass of the
# only thing keeping this gate enforcing. Collapse quoting and line continuations
# before matching so the string the shell builds is what gets checked.
_LINE_CONTINUATION_RE = re.compile(r"\\\n[ \t]*")
_SHELL_QUOTING_RE = re.compile(r"['\"\\]")


def _shell_normalized(recipe: str) -> str:
    """Collapse shell quoting/escaping so concatenation-obfuscated flags match."""
    return _SHELL_QUOTING_RE.sub("", _LINE_CONTINUATION_RE.sub("", recipe))


def check_self_invocation(repo_root: Path, contract: ArtifactContract) -> list[Finding]:
    """Verify the detector is wired into ``make check`` and runs enforcing."""
    makefile = repo_root / (contract.artifact_container or "Makefile")
    target = contract.artifact
    if not makefile.is_file():
        return [
            Finding(
                contract_id=contract.contract_id,
                edge_class=EdgeClass.NEVER_FIRED,
                producer_side=contract.producer,
                consumer_side=contract.consumer,
                evidence=f"{makefile} does not exist, so nothing invokes the wiring detector",
                remedy=f"add a '{target}' target invoking {CLI_MODULE} and make it a prerequisite of 'check'",
            )
        ]

    text = makefile.read_text(encoding="utf-8", errors="replace")
    problems: list[str] = []
    if target not in _prerequisites_of(text, "check"):
        problems.append(f"'{target}' is not a prerequisite of the 'check' target")
    recipe = _recipe_of(text, target)
    normalized = _shell_normalized(recipe)
    if not recipe.strip():
        problems.append(f"no '{target}:' target with a recipe exists")
    elif CLI_MODULE not in normalized:
        problems.append(f"the '{target}' recipe does not invoke {CLI_MODULE}")
    elif ADVISORY_FLAG in normalized:
        problems.append(
            f"the '{target}' recipe passes {ADVISORY_FLAG}, which downgrades the gate to a report — "
            "exactly the failure mode this detector exists to prevent"
        )
    if not problems:
        return []
    return [
        Finding(
            contract_id=contract.contract_id,
            edge_class=EdgeClass.NEVER_FIRED,
            producer_side=contract.producer,
            consumer_side=contract.consumer,
            evidence=(
                "the wiring detector is not enforced by its own build: "
                + "; ".join(problems)
                + f". Checked {makefile.name} for target {target!r} and module {CLI_MODULE!r}"
            ),
            remedy=(
                f"restore '{target}' in the 'check' prerequisite list and ensure its recipe runs "
                f"'{CLI_MODULE}' without {ADVISORY_FLAG}"
            ),
        )
    ]
