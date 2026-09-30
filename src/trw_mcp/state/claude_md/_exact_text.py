"""Byte-exact reading and joining for the two AGENTS.md writers (E2E-INC-051 follow-up).

The marker-region writer (``bootstrap/_opencode.py`` + ``replace_marker_region``) and the sync writer
(``_parser.render_merged_content``) both edit a file whose other bytes belong to the user. Reading it with
``Path.read_text`` translated CRLF to LF, and the append paths ``rstrip``-ed the user's trailing blank
lines, so a sync changed bytes outside TRW's region and, when the two writers disagreed about it, took
turns rewriting the file. Both now read with :func:`read_exact`, render TRW's block in the file's own line
ending (:func:`file_eol`, :func:`as_eol`), and join with :func:`append_block`, which adds a separator only
when the junction lacks one and never trims what the user wrote.
"""

from __future__ import annotations

from pathlib import Path


def read_exact(path: Path) -> str:
    """The file's text with NO newline translation (``\\r\\n`` stays ``\\r\\n``); UTF-8, errors raise as ``read_text``'s do."""
    return path.read_bytes().decode("utf-8")


def file_eol(text: str) -> str:
    """The line ending *text* uses: ``\\r\\n`` when CRLF lines outnumber bare-LF ones, else ``\\n``."""
    crlf = text.count("\r\n")
    return "\r\n" if crlf > text.count("\n") - crlf else "\n"


def as_eol(text: str, eol: str) -> str:
    """*text* with every line ending rewritten to *eol*."""
    return text.replace("\r\n", "\n").replace("\n", eol) if eol != "\n" else text.replace("\r\n", "\n")


def block_text(block: str, eol: str) -> str:
    """TRW's block in the file's line ending, ending with exactly one line ending."""
    return as_eol(block.strip("\n"), eol) + eol


def append_block(existing: str, block: str) -> str:
    """*existing* plus TRW's *block*, separated by a blank line only if the junction has none.

    Everything the user wrote stays byte-for-byte, trailing blank lines and CRLF included; whitespace-only
    content is replaced (it is not the user's work).
    """
    eol = file_eol(existing)
    tail = block_text(block, eol)
    if not existing.strip():
        return tail
    if existing.endswith(eol + eol):
        return existing + tail
    return existing + (eol if existing.endswith(eol) else eol + eol) + tail


def splice_block(existing: str, start: int, tail_start: int, block: str) -> str:
    """*existing* with ``[start, tail_start)`` replaced by *block*, in the file's line ending.

    *tail_start* is the offset just past the end marker's TEXT, so the tail begins with that line's own
    line ending: the block's final one is dropped then, and kept when the marker ends the file.
    """
    eol = file_eol(existing)
    body = block_text(block, eol)
    tail = existing[tail_start:]
    if tail.startswith(eol):
        body = body[: -len(eol)]
    return existing[:start] + body + tail
