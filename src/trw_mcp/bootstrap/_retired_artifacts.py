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

import os
import shlex
from pathlib import Path
from typing import Literal

from ._utils import printable

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
#: Every client skill directory that mirrors ``.claude/skills`` (the dir surfaces the retirement sweep covers).
CLIENT_SKILL_ROOTS: tuple[str, ...] = (".agents/skills", ".cursor/skills", ".github/skills", ".opencode/skills")


def _trw_agent_memory_dirs(target_dir: Path) -> tuple[str, ...]:
    """``.claude/agent-memory/trw-*``: TRW's namespace, and no TRW agent writes Claude Code memory since 8.0.

    Read from disk rather than a name list (REMOVE-S8a), so a retired agent's memory needs no registry entry.
    Only ``trw-`` subdirectories: the parent is shared with any agent the project defines.
    """
    root = target_dir / RETIRED_AGENT_MEMORY_DIR
    try:
        names = sorted(entry.name for entry in root.iterdir() if entry.name.startswith("trw-"))
    except (
        OSError
    ):  # trw-fail-silent-allow: no (or an unreadable) agent-memory dir means nothing to report; advice only
        return ()
    return tuple(f"{RETIRED_AGENT_MEMORY_DIR}/{name}" for name in names)


_RETIRED_SKILL_WHY = (
    "TRW retired this skill and removes only unchanged copies it wrote, so this one, edited or unrecorded, stayed"
)


def _retired_skill_mirrors(target_dir: Path) -> list[tuple[str, str]]:
    """Retired ``trw-`` skills still present in a client skills directory.

    A ``trw-*`` entry whose name is not in the full bundled skill set (REMOVE-S8a). The sweep judges a client
    mirror by what that client ships, so a curated-out copy it kept as unproven is not reported here yet
    (DOCTOR-PER-CLIENT-SKILL-PREDICATE). A live ``.claude/skills/<name>`` directory is the project's own skill (a retired name it kept),
    and its mirrors follow it (PRD-FIX-139-FR03, the same ``is_dir`` test the sweep uses, so a dangling link
    does not count); only a mirror whose source is gone is a leftover.
    """
    from ._utils import _DATA_DIR

    bundled = {entry.name for entry in (_DATA_DIR / "skills").iterdir() if entry.is_dir()}
    found: list[tuple[str, str]] = []
    for root in CLIENT_SKILL_ROOTS:
        try:
            entries = sorted((target_dir / root).iterdir())
        except OSError:  # trw-fail-silent-allow: an absent or unreadable mirror holds nothing to report; advice only
            continue
        for entry in entries:
            name = entry.name
            if not name.startswith("trw-") or name in bundled or (target_dir / ".claude" / "skills" / name).is_dir():
                continue
            found.append((f"{root}/{name}", _RETIRED_SKILL_WHY))
    return found


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


def _retired_artifacts(target_dir: Path) -> tuple[tuple[str, str], ...]:
    """Every retired artifact TRW reports, with what replaced it. A path is reported while it exists."""
    return (
        (RETIRED_OPENCODE_CONTRACT, "consolidated into trw-prd-ready/SKILL.md"),
        *((relpath, _AGENT_MEMORY_WHY) for relpath in _trw_agent_memory_dirs(target_dir)),
    )


def _shell_quote(path: str) -> str:
    """*path* as one shell word that is safe to print and decodes back to the same bytes.

    ``shlex.quote`` keeps control bytes raw (they would drive the terminal), and escaping its output changes
    the operand (codex S8a r2). A path with a non-printable character becomes ANSI-C ``$'...'`` (bash, zsh),
    where every such byte is a ``\\xHH`` escape the shell turns back into that byte. Bytes come from
    ``os.fsencode``, so a name that is not valid UTF-8 (surrogate-escaped on POSIX) decodes to its real bytes.
    """
    if path.isprintable():
        return shlex.quote(path)
    body = "".join(
        "\\" + ch if ch in "\\'" else ch if ch.isprintable() else "".join(f"\\x{b:02x}" for b in os.fsencode(ch))
        for ch in path
    )
    return f"$'{body}'"


def _removal_advice(target_dir: Path, relpath: str) -> tuple[str, str]:
    """``(what it holds, shell command)`` fitted to what *relpath* is; the command is rooted in *target_dir*.

    codex sol fix-delta round 1 on lane-infra-200-b: a relative operand is
    ambiguous outside the inspected project -- ``trw-mcp doctor /projects/B``
    run with a shell cwd of ``/projects/A`` would delete A's same-named file
    if the printed command were followed literally. The absolute path removes
    that ambiguity regardless of the operator's own cwd.

    round 2: ``shlex.quote`` (not a bare ``f"'{path}'"``) -- a project path
    containing a single quote otherwise breaks out of the quoting and lets the
    printed advice tokenize as more than one shell command if followed literally.

    A file gets ``rm``; a symlink ``rm`` (the link, never its target); an empty directory ``rmdir``; a directory
    with files ``rm -r`` and the count, so six agent-written notes are not advised away like an empty folder.
    """
    absolute = target_dir.resolve() / relpath
    quoted = _shell_quote(str(absolute))
    if absolute.is_symlink():
        return "it is a symlink; removing it leaves what it points at alone", f"rm {quoted}"
    if not absolute.is_dir():
        return "", f"rm {quoted}"
    try:
        entries = any(absolute.iterdir())
    except OSError:  # trw-fail-silent-allow: unreadable, so say nothing about its contents; advice only, never deleted
        return "", f"rm -r {quoted}"
    if not entries:
        return "it is empty", f"rmdir {quoted}"
    files = sum(len(names) for _dir, _subdirs, names in os.walk(absolute))
    if files:
        plural = files != 1
        return f"it holds {files} file{'s' if plural else ''}; review {'them' if plural else 'it'} before removing", (
            f"rm -r {quoted}"
        )
    return "it holds no files", f"rm -r {quoted}"


def _present(target_dir: Path) -> list[tuple[str, str]]:
    present = [(rel, why) for rel, why in _retired_artifacts(target_dir) if (target_dir / rel).exists()]
    present.extend(_retired_skill_mirrors(target_dir))
    if _is_retired_cursor_cli_rule(target_dir):
        present.append((RETIRED_CURSOR_CLI_RULE, _CURSOR_CLI_RULE_WHY))
    return present


def _advice_text(target_dir: Path, rel: str) -> str:
    """``[<what it holds>; ]remove it manually: <command>`` for *rel*."""
    holds, command = _removal_advice(target_dir, rel)
    return f"{holds + '; ' if holds else ''}remove it manually: {command}"


def retired_artifact_notices(target_dir: Path) -> list[str]:
    """One line per retired artifact present, naming what replaced it and its removal command."""
    # Names come from disk (REMOVE-S8a), so a name's control bytes are escaped before they reach a terminal.
    return [
        f"retired_artifact_present: {printable(rel)} is no longer used by TRW ({why}); {_advice_text(target_dir, rel)}"
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
            f"{printable(rel)} is retired and unused ({why}); {_advice_text(target_dir, rel)}" for rel, why in present
        ),
    )
