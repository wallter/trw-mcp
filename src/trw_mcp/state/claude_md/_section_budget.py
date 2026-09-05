"""Fit TRW's OWN generated section to the target file's line budget.

PRD-FIX-123-FR01 made the writer REFUSE an over-budget merge instead of slicing
the user's region to fit. That is right for user-authored bytes and wrong for
TRW's own output: at default config a sub-scope sync rendered a 94-line block
into a directory whose budget (``sub_claude_md_max_lines``) is 50, so the
generated section refused ITSELF and no file was ever created — even in an empty
directory, where ``current_non_generated_bytes`` was 0 and there was nothing to
protect.

The rule this module owns: **TRW may shrink its own section to fit; it may never
shrink the user's.** When the merged result overflows the budget, the sub-scope
section collapses to a pointer at the repository-root ``CLAUDE.md`` — the carrier
the client already loads alongside a nested file, so nothing an agent needs is
lost, only duplicated. If the pointer form still overflows, the excess is user
content and :func:`~trw_mcp.state.claude_md._write_guard.guarded_instruction_write`
refuses, unchanged.

Why sub scope only: at root the block IS the protocol carrier, and
PRD-CORE-247-FR09 requires the deliver gate to be stated there exactly once and
never zero times, so root may not degrade to a pointer. Root already has its own
size lever — PRD-CORE-203 externalization reduces the marker region to a single
``@.trw/INSTRUCTIONS.md`` import — and it is enabled by default
(``instruction_externalize="auto"``), so an over-budget root file is user content
by construction and refusal is the truthful outcome.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.state.claude_md._parser import (
    TRW_AUTO_COMMENT,
    TRW_MARKER_END,
    TRW_MARKER_START,
    render_merged_content,
)

logger = structlog.get_logger(__name__)

#: The scope whose section may collapse to a pointer (see the module docstring).
SUB_SCOPE = "sub"

#: The pointer body. Deliberately one paragraph: every line costs against a
#: budget measured in lines, and the shipped default leaves a nested file only
#: 50 of them to share with the user's own notes. It names tools an agent can
#: actually call rather than describing the protocol it is pointing away from.
_POINTER_BODY = (
    "**Call `trw_session_start()` before your first edit in this directory** — it loads prior "
    "learnings and recovers any active run. The full TRW protocol (tool lifecycle, memory routing, "
    "deliver gate) is carried by the repository-root `CLAUDE.md`, which your client loads alongside "
    "this file; call `trw_skill_discovery()` for the live tool surface and `trw_status()` for the "
    "phase you are in."
)


def render_pointer_section() -> str:
    """Render the compact pointer form of the generated section.

    Marker-wrapped exactly like the full section, so the merge writer replaces
    one with the other in place and a later sync under a raised budget restores
    the full form without leaving a second block behind.
    """
    return f"\n{TRW_AUTO_COMMENT}\n{TRW_MARKER_START}\n\n{_POINTER_BODY}\n\n{TRW_MARKER_END}\n"


def merged_line_count(target: Path, section: str) -> int:
    """Return the line count the write guard will measure for this merge.

    Deliberately the guard's own arithmetic (``split("\\n")`` on the merged
    candidate, not ``splitlines()``): a budget decision made on a different
    count than the one that refuses is a budget decision that can be wrong by
    one line in the direction that still refuses.
    """
    return len(render_merged_content(target, section).split("\n"))


def fit_section_to_budget(target: Path, section: str, max_lines: int | None, *, scope: str) -> str:
    """Return the section to write: the full one, or the pointer when it overflows.

    Args:
        target: The instruction file the section will be merged into.
        section: The fully rendered generated section.
        max_lines: The budget the writer will enforce, or ``None`` for no ceiling.
        scope: Sync scope; only ``"sub"`` may collapse (module docstring).

    Returns:
        *section* unchanged, or the pointer form when the merged result would
        exceed *max_lines*. Never a truncation of either party's content.
    """
    if scope != SUB_SCOPE or max_lines is None:
        return section
    full_lines = merged_line_count(target, section)
    if full_lines <= max_lines:
        return section
    pointer = render_pointer_section()
    logger.info(
        "instruction_section_collapsed_to_pointer",
        target=str(target),
        limit=max_lines,
        full_merged_lines=full_lines,
        pointer_merged_lines=merged_line_count(target, pointer),
    )
    return pointer


__all__ = ["SUB_SCOPE", "fit_section_to_budget", "merged_line_count", "render_pointer_section"]
