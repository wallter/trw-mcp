"""Whether TRW's marker lines identify exactly one block of an instruction file (CLAUDE-MD S1 red team B5).

Belongs to the ``_parser`` / ``_write_guard`` seam: the merge, the legacy-block migration, the guard, the orphan
strip and uninstall all ask this one question before they cut a span, so none of them guesses which span is TRW's.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trw_mcp.models.typed_dicts._ceremony import InstructionWriteRefusalDict


def marker_layout_problem(
    content: str,
    markers: tuple[str, str],
    *,
    repeats_allowed: bool = False,
) -> str | None:
    """Why TRW cannot tell its own block in *content* from the user's text, else ``None``.

    One well-formed block, or none, is unambiguous. A marker line inside a fenced code block, a start with no end
    (or an end with no start), a block opened inside another and, unless *repeats_allowed*, a second block all
    leave TRW with no evidence of which span is its own; a writer with no evidence must not write (CLAUDE-MD S1
    red team B5: the first-start-to-next-end guess deleted the user's text between two starts).
    """
    from trw_mcp.state.claude_md._instructions_link import fenced_line_indices

    start, end = markers
    fenced = fenced_line_indices(content)
    depth, blocks = 0, 0
    for index, line in enumerate(content.splitlines()):
        stripped = line.strip()
        if stripped not in (start, end):
            continue
        if index in fenced:
            return f"a {stripped} line sits inside a fenced code block"
        expanded = line.expandtabs(4)
        if len(expanded) - len(expanded.lstrip()) >= 4:
            # Markdown reads a line indented 4+ columns as code: an example, never TRW's own block (TRW writes its
            # markers at column 0). Refusing is the safe side of a list-nested marker misread as code.
            return f"a {stripped} line is indented as a code example"
        if stripped == start:
            if depth:
                return "a TRW block opens inside another one"
            depth = 1
        elif not depth:
            return "an end marker has no start marker above it"
        else:
            depth, blocks = 0, blocks + 1
    if depth:
        return "a start marker has no end marker below it"
    if blocks > 1 and not repeats_allowed:
        return f"it holds {blocks} TRW blocks, so TRW cannot tell which one is its own"
    return None


#: Refusal kinds that leave exactly one file as found, so there is nothing of theirs for a rollback to undo.
FILE_SCOPED_KINDS = frozenset({"ambiguous_markers"})


class FileScopedRefusal(str):
    """An ``errors`` entry that says which kind of refusal it is (DEMOTE-REFUSAL-STRUCTURED).

    A ``str`` so every result merge (``append``, ``extend``, ``in``) and every reader keeps working unchanged, and so
    the kind travels with the message through all of them: a separate result key would be dropped by each merge that
    copies only the keys it knows. Matching on the kind, never the text, means a path that happens to contain a
    refusal's wording cannot pass for one. Anything that rebuilds the string drops the kind, which keeps it an error:
    the rollback, the safe side.
    """

    kind: str

    def __new__(cls, text: str, kind: str) -> FileScopedRefusal:
        message = super().__new__(cls, text)
        message.kind = kind
        return message

    def __getnewargs__(self) -> tuple[str, str]:  # type: ignore[override]  # copy, deepcopy and pickle keep the kind
        return (str(self), self.kind)


def refusal_message(text: str, kind: str) -> str:
    """The ``errors`` entry for a refusal of *kind*: tagged when the kind is file-scoped, else the plain text."""
    return FileScopedRefusal(text, kind) if kind in FILE_SCOPED_KINDS else text


def demote_file_scoped_refusals(result: dict[str, list[str]]) -> int:
    """Move "this one file was left as found" messages from ``errors`` to ``warnings``; return how many moved.

    A file whose TRW markers do not identify one block is never written, so there is nothing of ITS to undo:
    leaving the message in ``errors`` makes update-project roll back every managed directory for a file it did
    not touch (UPDATE-ROLLBACK-FENCED-MARKER). The user still hears about it, in the warnings, with the fix.
    Every other error is left where it is: the match is on the kind the refusing site recorded
    (:class:`FileScopedRefusal`), never on the message text, which can hold a path with any words in it.
    """
    kept: list[str] = []
    moved = 0
    for message in result.get("errors", []):
        if isinstance(message, FileScopedRefusal) and message.kind in FILE_SCOPED_KINDS:
            result.setdefault("warnings", []).append(message)
            moved += 1
        else:
            kept.append(message)
    if moved:
        result["errors"][:] = kept
    return moved


def ambiguous_marker_refusal(
    target: Path, current: str, markers: tuple[str, str]
) -> InstructionWriteRefusalDict | None:
    """The refusal for a file whose TRW markers do not identify one block, else ``None`` (CLAUDE-MD S1 red team B5).

    Not even ``force`` writes then: TRW may only replace its own uniquely identified block, and with duplicate,
    nested, unbalanced or fenced markers it cannot tell that block from the user's text.
    """
    from trw_mcp.state.claude_md._write_measure import build_refusal  # lazy: _write_measure imports _parser

    problem = marker_layout_problem(current, markers)
    if problem is None:
        return None
    return build_refusal(
        target,
        "ambiguous_markers",
        f"left as found: {problem}. Remove the stray trw:start/trw:end lines (or move an example out of its "
        "code fence) and run the command again",
        lines=0,
        limit=0,
        counts=(0, 0, 0, 0),
    )
