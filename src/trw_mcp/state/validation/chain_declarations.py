"""A PRD's declared call chains, parsed from its traceability matrix (PRD-CORE-320 FR08).

The ``Call chain`` cell of an FR's traceability row holds backticked fully-qualified
symbols joined by ``->``; :func:`trw_mcp.state.validation.call_chain.verify_chain`
checks each one. Parsing PRD text lives here, apart from verifying code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ChainDeclaration:
    """One PRD traceability row's ``Call chain`` cell.

    ``chain`` is empty and ``malformed`` is ``False`` for "no claim" (a blank cell,
    ``—``, ``-`` or ``(planned)``) — the row is omitted by :func:`declared_chains`.
    ``malformed`` is ``True`` when the cell could not be parsed at all (unbalanced
    backticks, an empty hop, or a non-identifier symbol): reported ``isolated`` with
    reason ``"malformed chain"`` at deliver time, never silently skipped.
    """

    chain: tuple[str, ...] = ()
    malformed: bool = False


#: Cell contents that mean "no claim yet" — omitted from the returned mapping.
_NO_CALL_CHAIN_CLAIM: frozenset[str] = frozenset({"", "—", "-", "(planned)"})

_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
_BACKTICKED_HOP_RE = re.compile(r"`([^`]*)`")


def _split_table_row(line: str) -> list[str] | None:
    """Split a ``| a | b | c |`` markdown table line into trimmed cells, or ``None``."""
    match = _TABLE_ROW_RE.match(line)
    if match is None:
        return None
    return [cell.strip() for cell in match.group(1).split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(_SEPARATOR_CELL_RE.match(cell) for cell in cells)


def _valid_hop_text(text: str) -> bool:
    """A hop is either ``a.b.c`` (dotted identifiers) or a ``hook:``/``cli:`` entry."""
    if not text:
        return False
    prefix, sep, rest = text.partition(":")
    if sep:
        return prefix in {"hook", "cli"} and bool(rest.strip())
    return all(part.isidentifier() for part in text.split("."))


def _parse_chain_cell(cell: str) -> ChainDeclaration:
    """Parse one ``Call chain`` cell into a :class:`ChainDeclaration`."""
    text = cell.strip()
    if text in _NO_CALL_CHAIN_CLAIM:
        return ChainDeclaration()
    if text.count("`") % 2 != 0:
        return ChainDeclaration(malformed=True)
    hops = _BACKTICKED_HOP_RE.findall(text)
    between = "".join(_BACKTICKED_HOP_RE.sub("\x00", text).split())  # what lies outside the backticked hops
    if not hops or any(not _valid_hop_text(hop) for hop in hops) or between != "\x00" + "->\x00" * (len(hops) - 1):
        # Only ``->`` may separate hops: an unbackticked hop was silently dropped, shortening the chain (sol s2 r1).
        return ChainDeclaration(malformed=True)
    return ChainDeclaration(chain=tuple(hops))


@dataclass(frozen=True)
class MarkdownTable:
    """One markdown table: its header cells and its data rows (each a tuple of trimmed cells)."""

    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


def markdown_tables(text: str) -> list[MarkdownTable]:
    """Every markdown table in *text*, in document order. Never raises.

    A table is a maximal run of ``| ... |`` lines. Its first line is the header;
    a separator row immediately after it is dropped; every later line is a data
    row, whatever its cell count (callers check the width they need).

    Runtime callers: :func:`declared_chains` (PRD-CORE-320 FR08, reached from
    ``trw_deliver`` through ``step_capability_integration`` and, via the
    PRD-CORE-321 baseline, ``compute_requirement_drift``) and
    ``trw_mcp.state.validation.requirement_drift.amendment_rows`` (PRD-CORE-321
    FR04). Soundness scope: proves which ``|``-delimited lines group into which
    table; a ``|`` inside a cell's backticks still splits the cell.
    """
    tables: list[MarkdownTable] = []
    run: list[list[str]] = []
    for line in [*text.splitlines(), ""]:
        cells = _split_table_row(line)
        if cells is not None:
            run.append(cells)
            continue
        if run:
            header, *rows = run
            if rows and _is_separator_row(rows[0]):
                rows = rows[1:]
            tables.append(MarkdownTable(tuple(header), tuple(tuple(row) for row in rows)))
            run = []
    return tables


def declared_chains(prd_text: str) -> dict[str, ChainDeclaration]:
    """Every FR/NFR id's declared call chain from *prd_text*'s traceability matrix.

    Finds each markdown table (:func:`markdown_tables`) whose header row has a
    ``Call chain`` column (case insensitive) and keys each data row by its first
    cell (e.g. ``"FR02"``). A row with no claim (see :class:`ChainDeclaration`)
    is omitted entirely; a malformed cell is kept, marked ``malformed=True``, so a
    caller never mistakes it for "no claim". A PRD with no such column returns an
    empty mapping. Never raises.
    """
    result: dict[str, ChainDeclaration] = {}
    for table in markdown_tables(prd_text):
        column = next((i for i, cell in enumerate(table.header) if cell.lower() == "call chain"), None)
        if column is None:
            continue
        for cells in table.rows:
            if len(cells) > column:
                fr_id = cells[0].strip()
                declaration = _parse_chain_cell(cells[column])
                if fr_id and (declaration.chain or declaration.malformed):
                    result[fr_id] = declaration
    return result
