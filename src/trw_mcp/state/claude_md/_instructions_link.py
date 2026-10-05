"""The generated TRW instructions file and the AGENTS.md link to it (PRD-CORE-341).

Belongs to the ``state/claude_md`` package. AGENTS.md is the project's file, so
TRW's block in it is two lines: a sentence naming ``.trw/INSTRUCTIONS.md`` for
clients that do not expand imports, and the ``@`` import Claude Code expands.
The instruction body lives in that TRW-owned file, rewritten whole by every
AGENTS.md writer (sync, ``init-project``/``update-project`` for claude-code and
grok, the cursor-cli installer). Nothing here reads the learning store, so the
output depends only on the render (PRD-CORE-341-FR01/NFR01).

HB-2: a file at the path without TRW's header is the user's and is never
written (FR07); a generated one is replaced through ``guarded_instruction_write``,
which keeps the previous bytes under the backup directory first (FR02).
"""

from __future__ import annotations

import re
from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md._parser import TRW_AUTO_COMMENT, TRW_MARKER_END, TRW_MARKER_START
from trw_mcp.state.claude_md._write_guard import InstructionWriteVerdict, guarded_instruction_write
from trw_mcp.state.claude_md._write_measure import build_refusal

INSTRUCTIONS_RELPATH = ".trw/INSTRUCTIONS.md"
#: Shared with the pre-8.0 sidecar generator, so its stale copies count as TRW's own.
GENERATED_HEADER_PREFIX = "<!-- TRW AUTO-GENERATED — do not edit."
_HEADER = f"{GENERATED_HEADER_PREFIX} Rewritten by trw-mcp instructions sync. -->"

LINK_BODY = (
    f"TRW workflow, tools and deliver gate: [{INSTRUCTIONS_RELPATH}]({INSTRUCTIONS_RELPATH}). "
    "If your client did not load the import below, read that file before starting work.\n"
    f"@{INSTRUCTIONS_RELPATH}\n"
)


def agents_link_section() -> str:
    """The marker-delimited AGENTS.md section every writer merges (identical bytes, so writers never churn)."""
    return f"{TRW_AUTO_COMMENT}\n{TRW_MARKER_START}\n\n{LINK_BODY}\n{TRW_MARKER_END}\n"


#: TRW's block in a user's own root CLAUDE.md (operator ruling 2026-10-01). Claude Code skips AGENTS.md while a
#: CLAUDE.md exists, so the block imports TRW's context directly and names AGENTS.md in plain text only: the two
#: files may differ, and importing one into the other would repeat or contradict the user's own instructions.
#: A CLAUDE.md that already imports AGENTS.md (and so TRW's import) gets no block (``link_claude_md``).
CLAUDE_LINK_BODY = (
    f"TRW workflow, tools and deliver gate: [{INSTRUCTIONS_RELPATH}]({INSTRUCTIONS_RELPATH}), imported below. "
    "Claude Code does not load AGENTS.md while this CLAUDE.md exists; if this project also keeps instructions "
    "in AGENTS.md, read that file too.\n"
    f"@{INSTRUCTIONS_RELPATH}\n"
)


#: CommonMark: a fence is 3+ backticks or tildes indented at most 3 spaces; it closes on a run of the same
#: character at least as long, with nothing after it but whitespace.
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def fenced_line_indices(text: str) -> set[int]:
    """Indices of the lines of *text* inside a fenced code block, the fence lines themselves included."""
    fenced: set[int] = set()
    fence: str | None = None
    for index, line in enumerate(text.splitlines()):
        match = _FENCE.match(line)
        if fence is None:
            # A backtick opener whose info string holds a backtick is inline code, not a fence (CommonMark 4.5).
            if match and not (match.group(1)[0] == "`" and "`" in match.group(2)):
                fence = match.group(1)
                fenced.add(index)
            continue
        fenced.add(index)
        if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence) and not match.group(2).strip():
            fence = None
    return fenced


#: Claude Code's import syntax: an ``@path`` token at the start of a line or after whitespace.
_IMPORT_TOKEN = re.compile(r"(?:^|\s)@(\S+)", re.MULTILINE)
#: CommonMark code span: a run of N backticks closed by the next run of exactly N.
_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`).*?(?<!`)\1(?!`)")


def live_imports(text: str) -> list[str]:
    """The ``@path`` imports Claude Code expands from *text*, in order; code spans and fenced blocks are inert."""
    fenced = fenced_line_indices(text)
    live = "\n".join(_CODE_SPAN.sub("", line) for i, line in enumerate(text.splitlines()) if i not in fenced)
    return _IMPORT_TOKEN.findall(live)


def claude_md_link_section() -> str:
    """The marker-delimited section TRW keeps in an existing root CLAUDE.md (never AGENTS.md imported)."""
    return f"{TRW_AUTO_COMMENT}\n{TRW_MARKER_START}\n\n{CLAUDE_LINK_BODY}\n{TRW_MARKER_END}\n"


def render_instructions_file(body: str) -> str:
    """The whole ``.trw/INSTRUCTIONS.md``: the generated header, then *body*."""
    return f"{_HEADER}\n\n{body.strip()}\n"


def render_instructions_body(project_root: Path, config: TRWConfig | None = None, client: str = "auto") -> str:
    """The one body every writer of ``.trw/INSTRUCTIONS.md`` renders (sync, init, update, every client).

    The file is shared, so no writer may render its own variant: a body picked by the writer's own
    client profile made ``init-project`` and ``instructions sync`` rewrite each other (cursor-cli wrote
    its light body under a ``# TRW Ceremony Protocol`` heading; sync wrote the full section). The
    choice follows the configured ceremony mode (``effective_ceremony_mode`` already resolves the
    active client profile), and ``codex`` renders its own profile's block.
    """
    from trw_mcp.bootstrap._utils import detect_ide
    from trw_mcp.models.config import get_config
    from trw_mcp.state.claude_md._static_sections import render_agents_trw_section, render_minimal_protocol

    config = config or get_config()
    effective_client = client
    if client == "auto":
        detected = detect_ide(project_root)
        if "codex" in detected and "opencode" not in detected:
            effective_client = "codex"
    if effective_client == "codex":
        from trw_mcp.models.config._profiles import resolve_client_profile

        return render_agents_trw_section(client_profile=resolve_client_profile("codex"))
    if config.effective_ceremony_mode == "light":
        return render_minimal_protocol()
    return render_agents_trw_section()


def _user_authored(target: Path) -> bool:
    if not target.is_file():
        return False
    with target.open(encoding="utf-8", errors="replace") as handle:
        return not handle.readline().startswith(GENERATED_HEADER_PREFIX)


def write_instructions_file(
    project_root: Path,
    *,
    dry_run: bool = False,
    config: TRWConfig | None = None,
    client: str = "auto",
) -> InstructionWriteVerdict:
    """Render and write ``.trw/INSTRUCTIONS.md``, or refuse when a user wrote that file.

    The body is :func:`render_instructions_body`; callers never supply one.

    A caller writes AGENTS.md's link only after this returns without a refusal,
    so the import never points at content TRW did not render.
    """
    target = project_root / INSTRUCTIONS_RELPATH
    if _user_authored(target):
        size = target.stat().st_size
        return InstructionWriteVerdict(
            written=False,
            refusal=build_refusal(
                target,
                "user_authored",
                f"{INSTRUCTIONS_RELPATH} has no TRW header, so it is yours: move it aside and sync again",
                lines=0,
                limit=0,
                counts=(size, size, size, size),
            ),
        )
    return guarded_instruction_write(
        target,
        render_instructions_file(render_instructions_body(project_root, config, client)),
        enforce_shrink_floor=False,
        dry_run=dry_run,
        config=config,
        project_root=project_root,
    )


__all__ = [
    "GENERATED_HEADER_PREFIX",
    "INSTRUCTIONS_RELPATH",
    "LINK_BODY",
    "agents_link_section",
    "live_imports",
    "render_instructions_body",
    "render_instructions_file",
    "write_instructions_file",
]
