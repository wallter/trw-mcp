"""PRD-INFRA-200 FR05: report -- never delete -- a specific retired dead file.

Three codex sol fix-delta rounds against an earlier "delete any orphaned
manifest key" design each found a real way an untrusted or merely stale
manifest could turn ``dropped_manifest_keys``' new ``unlink()`` into an
arbitrary or racy file deletion (escaping ``target_dir``, landing on an
unmanaged in-repo file, a same-run recorder exception, a parent-directory
symlink swap). The lead's redesign removes the decision entirely: an update
never deletes anything here. It only reports the specific retired
artifacts listed in :func:`_retired_artifacts`, so an operator can remove them by hand.

Belongs to the ``_update_project.py`` / ``_tombstones.py`` write path (the
notice) and the ``_subcommands_doctor.py`` facade (the doctor row); both read
:func:`_retired_artifacts` from here so there is exactly one place that
names each path.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Literal

#: A doctor-row (status, message) pair -- matches ``_doctor_environment.Row``
#: without importing the server layer from bootstrap (Class E: bootstrap must
#: not depend on server).
_Row = tuple[Literal["PASS", "WARN"], str]

#: Repo-relative path of the OpenCode-target self-copy of trw-prd-ready/SKILL.md,
#: retired by 0f5fba2cb (PRD-CORE-291-FR04 consolidated its content into the
#: canonical skill; nothing has written this path since).
RETIRED_OPENCODE_CONTRACT = ".opencode/skills/trw-prd-ready/trw-prd-ready-contract.md"

#: Claude Code's project-scope agent memory. Until 8.0 every bundled agent declared ``memory: project``,
#: so Claude Code wrote each TRW agent's notes to ``<dir>/<agent name>/``; trw_learn/trw_recall is now the one
#: durable agent memory. Only the subdirectories named after TRW's own agents are reported: the parent is
#: shared with any other agent a project defines, whose memory stays in use and is never TRW's to advise on.
RETIRED_AGENT_MEMORY_DIR = ".claude/agent-memory"
_AGENT_MEMORY_WHY = (
    "TRW agents no longer write Claude Code agent memory; record anything worth keeping with trw_learn first"
)
#: The generated distill explorer agent (``channels/claude_code/_explorer_subagent.py``) is not in ``data/agents``.
_GENERATED_AGENT_NAMES = ("trw-distill-explorer",)


def _trw_agent_memory_dirs() -> tuple[str, ...]:
    """``.claude/agent-memory/<name>`` for every agent TRW installs, bundled or generated."""
    from ._utils import _DATA_DIR

    names = {path.stem for path in (_DATA_DIR / "agents").glob("*.md")} | set(_GENERATED_AGENT_NAMES)
    return tuple(f"{RETIRED_AGENT_MEMORY_DIR}/{name}" for name in sorted(names))


#: cursor-cli's copy of the cursor rule file. Before PRD-CORE-301-FR14 a cursor-cli install wrote the protocol here AND
#: into AGENTS.md, and cursor-cli auto-loads both. The path is also cursor-ide's carrier, so it is reported only when
#: the project records no cursor-ide and the file lacks the cursor-ide appendix.
RETIRED_CURSOR_CLI_RULE = ".cursor/rules/trw-ceremony.mdc"
_CURSOR_CLI_RULE_WHY = "cursor-cli reads the TRW protocol from AGENTS.md, so this copy loads it a second time"


def _is_retired_cursor_cli_rule(target_dir: Path) -> bool:
    """True when the cursor rule file is cursor-cli's retired copy rather than cursor-ide's carrier."""
    from ._cursor import _CURSOR_IDE_APPENDIX
    from ._template_claude_md import _recorded_targets

    try:
        # Line endings normalized: a CRLF checkout of cursor-ide's file is still cursor-ide's (core301-fr14-b r1).
        content = (target_dir / RETIRED_CURSOR_CLI_RULE).read_bytes().replace(b"\r\n", b"\n")
    except OSError:  # trw-fail-silent-allow: an absent or unreadable file is not reported; nothing is ever deleted
        return False
    return "cursor-ide" not in _recorded_targets(target_dir) and _CURSOR_IDE_APPENDIX.strip().encode() not in content


def _retired_artifacts() -> tuple[tuple[str, str], ...]:
    """Every retired artifact TRW reports, with what replaced it. A path is reported while it exists."""
    return (
        (RETIRED_OPENCODE_CONTRACT, "consolidated into trw-prd-ready/SKILL.md"),
        *((relpath, _AGENT_MEMORY_WHY) for relpath in _trw_agent_memory_dirs()),
    )


def _removal_command(target_dir: Path, relpath: str) -> str:
    """A shell command rooted in *target_dir*, never a bare relative path.

    codex sol fix-delta round 1 on lane-infra-200-b: a relative operand is
    ambiguous outside the inspected project -- ``trw-mcp doctor /projects/B``
    run with a shell cwd of ``/projects/A`` would delete A's same-named file
    if the printed command were followed literally. The absolute path removes
    that ambiguity regardless of the operator's own cwd.

    round 2: ``shlex.quote`` (not a bare ``f"'{path}'"``) -- a project path
    containing a single quote otherwise breaks out of the quoting and lets the
    printed advice tokenize as more than one shell command if followed literally.
    """
    absolute = target_dir.resolve() / relpath
    verb = "rm -r" if absolute.is_dir() else "rm"
    return f"{verb} {shlex.quote(str(absolute))}"


def _present(target_dir: Path) -> list[tuple[str, str]]:
    present = [(rel, why) for rel, why in _retired_artifacts() if (target_dir / rel).exists()]
    if _is_retired_cursor_cli_rule(target_dir):
        present.append((RETIRED_CURSOR_CLI_RULE, _CURSOR_CLI_RULE_WHY))
    return present


def retired_artifact_notices(target_dir: Path) -> list[str]:
    """One line per retired artifact present, naming what replaced it and its removal command."""
    return [
        f"retired_artifact_present: {rel} is no longer used by TRW ({why}); "
        f"remove it manually: {_removal_command(target_dir, rel)}"
        for rel, why in _present(target_dir)
    ]


def retired_artifact_row(target_dir: Path) -> _Row:
    """Doctor status/message pair: WARN naming each present artifact and its removal command, else PASS."""
    present = _present(target_dir)
    if not present:
        return "PASS", "no retired dead files present"
    return (
        "WARN",
        "; ".join(
            f"{rel} is retired and unused ({why}); remove it manually: {_removal_command(target_dir, rel)}"
            for rel, why in present
        ),
    )
