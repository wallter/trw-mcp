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

import structlog

from ._utils import printable

logger = structlog.get_logger(__name__)

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


_RETIRED_AGENT_WHY = "TRW no longer ships this agent and removes only an unchanged copy it wrote, so this one, edited or unrecorded, stayed"


#: ``trw-*`` agents TRW once shipped and withdrew. A name here is positive provenance that a copy on disk is TRW's.
RETIRED_AGENT_STEMS = frozenset(
    {
        "trw-code-simplifier",
        "trw-requirement-writer",
        "trw-tester",
        "trw-traceability-checker",
        # The seven reviewer agents commit 2a9bc8d90b withdrew; they carry no ``trw-`` prefix.
        "reviewer-correctness",
        "reviewer-integration",
        "reviewer-performance",
        "reviewer-security",
        "reviewer-spec-compliance",
        "reviewer-style",
        "reviewer-test-quality",
    }
)


def _recorded_agent_stems(target_dir: Path) -> set[str]:
    """Stems of agents the project's manifest records TRW as having written (a ``content_hashes`` entry)."""
    from ._version_manifest import _manifest_content_hashes, _manifest_key_path, _read_manifest

    hashes = _manifest_content_hashes(_read_manifest(target_dir)) or {}
    return {Path(p).stem for p in map(_manifest_key_path, hashes) if p.startswith(".claude/agents/")}


def trw_agent_provenance(target_dir: Path, dest: Path, suffix: str) -> tuple[list[str], list[str]]:
    """``(retired, unknown)`` ``trw-*`` file names in the agent directory *dest* that TRW does not ship.

    *retired* have positive provenance (:data:`RETIRED_AGENT_STEMS`, or the manifest records TRW writing them):
    only those are ever advised away. *unknown* is a ``trw-*`` name with no provenance, which may be the
    project's own: reported, never advised away. An unavailable or empty bundle yields ``([], [])`` and a log
    line, so no installed agent can look retired because the roster could not be read (feedback #139).
    """
    from ._utils import _DATA_DIR

    shipped = {path.stem for path in (_DATA_DIR / "agents").glob("*.md")} if (_DATA_DIR / "agents").is_dir() else set()
    if not shipped:
        logger.warning("retired_agents_not_judged", reason="bundle_unavailable", bundle=str(_DATA_DIR / "agents"))
        return [], []
    try:
        names = sorted(entry.name for entry in dest.iterdir() if entry.is_file())
    except OSError:  # trw-fail-silent-allow: an absent or unreadable agent dir holds nothing to report; advice only
        return [], []
    provenance = RETIRED_AGENT_STEMS | _recorded_agent_stems(target_dir)
    from ._version_manifest import _manifest_content_hashes, _read_manifest
    from ._version_migration_clients import _is_channel_artifact

    hashes = _manifest_content_hashes(_read_manifest(target_dir)) or {}
    candidates = [
        n
        for n in names
        if n.endswith(suffix)
        and n.removesuffix(suffix) not in shipped
        # TRW's namespace, or a name TRW itself withdrew (the reviewer-* agents carry no trw- prefix).
        and (n.startswith("trw-") or n.removesuffix(suffix) in RETIRED_AGENT_STEMS)
        # An agent an active channel renders (CC-05 / OpenCode / Antigravity explorers) is that channel's, not retired.
        and not _is_channel_artifact(dest / n, hashes, target_dir)
    ]
    return (
        [n for n in candidates if n.removesuffix(suffix) in provenance],
        [n for n in candidates if n.removesuffix(suffix) not in provenance],
    )


def _retired_agents(target_dir: Path) -> list[tuple[str, str]]:
    """Every client's agent directory entry that is a ``trw-*`` agent with provenance of being TRW's, now withdrawn."""
    from trw_mcp.agents.agent_formats import agent_format_for
    from trw_mcp.models.config._profiles import builtin_client_ids

    found: dict[str, str] = {}
    for client in builtin_client_ids():
        fmt = agent_format_for(client)
        if not fmt.supports_agents or fmt.destination_dir is None:
            continue
        retired, _unknown = trw_agent_provenance(target_dir, target_dir / fmt.destination_dir, fmt.filename_suffix)
        for name in retired:
            found.setdefault(f"{fmt.destination_dir}/{name}", _RETIRED_AGENT_WHY)
    return list(found.items())


_RETIRED_SKILL_WHY = (
    "TRW retired this skill and removes only unchanged copies it wrote, so this one, edited or unrecorded, stayed"
)
_CURATED_OUT_SKILL_WHY = (
    "TRW no longer ships this skill to this client and removes only unchanged copies it wrote, so this one,"
    " edited or unrecorded, stayed"
)


def curated_out_reason(rel: str) -> str:
    """Why a removed client-mirror skill file went: TRW ships the skill, but no longer to that client; else ``""``."""
    from ._utils import _DATA_DIR
    from ._version_migration_clients import client_skill_lists

    for root, listed in client_skill_lists().items():
        prefix = f"{root}/"
        if listed is None or not rel.startswith(prefix):
            continue
        name = rel[len(prefix) :].split("/", 1)[0]
        if name.startswith("trw-") and name not in listed and (_DATA_DIR / "skills" / name).is_dir():
            return "TRW no longer ships this skill to this client"
    return ""


def _retired_skill_mirrors(target_dir: Path) -> list[tuple[str, str]]:
    """``trw-`` skills a client skills directory holds that TRW no longer ships to that client.

    Judged as the stale sweep judges it (DOCTOR-PER-CLIENT-SKILL-PREDICATE): against that client's own list
    (``client_skill_lists``), so a copy the sweep kept as unproven is always named here. A flag-gated skill is
    ``retire_disabled_skills``' on every surface and never named; a client whose list is unknown is not judged.
    A name TRW ships elsewhere is curated out of this client. A name TRW does not ship at all is retired, unless a
    live ``.claude/skills/<name>`` keeps it as the project's own skill: its mirrors follow it (PRD-FIX-139-FR03,
    the same ``is_dir`` test the sweep uses, so a dangling link does not count).
    """
    from ._optional_skills import CONDITIONAL_SKILLS
    from ._utils import _DATA_DIR
    from ._version_migration_clients import client_skill_lists

    bundled = {entry.name for entry in (_DATA_DIR / "skills").iterdir() if entry.is_dir()}
    shipped = client_skill_lists()
    found: list[tuple[str, str]] = []
    for root in CLIENT_SKILL_ROOTS:
        listed = shipped.get(root)
        if listed is None:
            continue
        try:
            entries = sorted((target_dir / root).iterdir())
        except OSError:  # trw-fail-silent-allow: an absent or unreadable mirror holds nothing to report; advice only
            continue
        for entry in entries:
            name = entry.name
            if not name.startswith("trw-") or name in listed or name in CONDITIONAL_SKILLS:
                continue
            if name in bundled:
                found.append((f"{root}/{name}", _CURATED_OUT_SKILL_WHY))
            elif not (target_dir / ".claude" / "skills" / name).is_dir():
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
    present.extend(_retired_agents(target_dir))
    if _is_retired_cursor_cli_rule(target_dir):
        present.append((RETIRED_CURSOR_CLI_RULE, _CURSOR_CLI_RULE_WHY))
    return present


def _advice_text(target_dir: Path, rel: str) -> str:
    """``[<what it holds>; ]remove it manually: <command>`` for *rel*."""
    holds, command = _removal_advice(target_dir, rel)
    return f"{holds + '; ' if holds else ''}remove it manually: {command}"


def retired_artifact_paths(target_dir: Path) -> list[str]:
    """The relative paths of every retired artifact present, the structured twin of :func:`retired_artifact_notices`."""
    return [rel for rel, _why in _present(target_dir)]


def retired_artifact_notices(target_dir: Path) -> list[str]:
    """One line per retired artifact present, naming what replaced it and its removal command."""
    # Names come from disk (REMOVE-S8a), so a name's control bytes are escaped before they reach a terminal.
    return [
        f"retired_artifact_present: {printable(rel)} is no longer used by TRW ({why}); {_advice_text(target_dir, rel)}"
        for rel, why in _present(target_dir)
    ]


def retired_artifact_row(target_dir: Path) -> _Row:
    """Doctor status/message pair: WARN naming each present artifact (one per line) and its removal command, else PASS."""
    present = _present(target_dir)
    if not present:
        return "PASS", "no retired dead files present"
    return (
        "WARN",
        "\n".join(  # one artifact per line (feedback #141); each name is escaped, so none can forge a line
            f"{printable(rel)} is retired and unused ({why}); {_advice_text(target_dir, rel)}" for rel, why in present
        ),
    )
