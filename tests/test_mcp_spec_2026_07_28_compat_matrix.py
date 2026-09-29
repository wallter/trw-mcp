"""PRD-CORE-317-FR02/FR03: load and validate the 2026-07-28 changelog matrix.

The matrix lives in ``docs/requirements-aare-f/prds/PRD-CORE-317.md`` section 6
(not a separate document, so it stays version-controlled alongside the PRD it
is scoped by). This test locates the matrix by its heading text -- never by a
hardcoded line number, which would drift the moment the PRD is edited -- and
asserts:

- exactly 20 rows total (9 Major + 11 Minor changelog items, per the PRD's
  Background section)
- every row's two applicability cells and its status cell are non-empty
- the three OAuth-hardening rows (FR03) are recorded Not Applicable and the
  matrix states the loopback-only trigger that would reopen the question
- the PRD's cited fastmcp/mcp pin lines and version constraints (NFR02) still
  match the live pyproject.toml pins

This is a monorepo-only test: the public trw-mcp mirror ships the package
alone, with no ``docs/`` PRD tree to read.

FR03 (the OAuth-hardening applicability determination) is itself
inspection-only per the PRD's own verification mapping (``automated: false``
-- "an applicability determination against a spec section is a documentation/
analysis property, not a machine-checkable behavior"); the machine-checkable
slice this file adds is that the matrix's own text records the determination
and its trigger, not a fresh applicability judgement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._layout import MONOREPO_ROOT, requires_monorepo

pytestmark = requires_monorepo

_MATRIX_HEADING = "### 2026-07-28 changelog matrix"

_EXPECTED_MAJOR_ROWS = 9
_EXPECTED_MINOR_ROWS = 11
_EXPECTED_TOTAL_ROWS = _EXPECTED_MAJOR_ROWS + _EXPECTED_MINOR_ROWS


@dataclass(frozen=True)
class MatrixRow:
    row_id: str
    changelog_item: str
    applies_stdio: str
    applies_daemon: str
    status: str
    action: str


def _prd_path() -> Path:
    assert MONOREPO_ROOT is not None  # narrowed by requires_monorepo skip
    path = Path(MONOREPO_ROOT) / "docs" / "requirements-aare-f" / "prds" / "PRD-CORE-317.md"
    assert path.is_file(), f"PRD-CORE-317.md not found at {path}"
    return path


def _split_table_row(line: str) -> list[str]:
    """Split a ``| a | b | c |`` markdown row into its cell texts, trimmed."""
    stripped = line.strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")]


_SEPARATOR_RE = re.compile(r"^\|?[\s:|-]+\|?$")


def _parse_matrix_tables(markdown: str) -> list[MatrixRow]:
    """Parse the two markdown tables (Major, Minor) following the matrix heading.

    Locates the heading by text, then scans forward collecting table rows
    until the next ``##``/``###`` heading or the ``---`` section break, so the
    parser survives edits elsewhere in the PRD without a hardcoded line range.
    """
    lines = markdown.splitlines()
    heading_index = next((i for i, line in enumerate(lines) if line.startswith(_MATRIX_HEADING)), None)
    assert heading_index is not None, f"heading {_MATRIX_HEADING!r} not found in PRD-CORE-317.md"

    rows: list[MatrixRow] = []
    in_header = True  # first table row after a blank line is a header, then a separator
    seen_header_for_block = False
    for line in lines[heading_index + 1 :]:
        text = line.strip()
        if text.startswith("## ") or (text.startswith("### ") and not text.startswith(_MATRIX_HEADING)):
            break
        if text == "---":
            break
        if not text.startswith("|"):
            # Blank line or prose between/around tables resets to "expect a header next".
            if text:
                continue
            seen_header_for_block = False
            in_header = True
            continue
        if not seen_header_for_block:
            # This is the header row of a (possibly new) table.
            seen_header_for_block = True
            in_header = True
            continue
        if in_header and _SEPARATOR_RE.match(text):
            in_header = False
            continue
        cells = _split_table_row(text)
        if len(cells) < 6:
            continue
        rows.append(
            MatrixRow(
                row_id=cells[0],
                changelog_item=cells[1],
                applies_stdio=cells[2],
                applies_daemon=cells[3],
                status=cells[4],
                action=cells[5],
            )
        )
    return rows


@pytest.fixture(scope="module")
def prd_markdown() -> str:
    return _prd_path().read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def matrix_rows(prd_markdown: str) -> list[MatrixRow]:
    return _parse_matrix_tables(prd_markdown)


def test_matrix_has_exactly_20_rows(matrix_rows: list[MatrixRow]) -> None:
    assert len(matrix_rows) == _EXPECTED_TOTAL_ROWS, (
        f"expected {_EXPECTED_TOTAL_ROWS} changelog rows "
        f"({_EXPECTED_MAJOR_ROWS} Major + {_EXPECTED_MINOR_ROWS} Minor per "
        f"PRD-CORE-317's Background), found {len(matrix_rows)}: "
        f"{[r.row_id for r in matrix_rows]}"
    )


def _row_ids(rows: list[MatrixRow]) -> list[str]:
    return [row.row_id for row in rows]


@pytest.mark.parametrize(
    "row_id",
    [f"M{i}" for i in range(1, _EXPECTED_MAJOR_ROWS + 1)] + [f"N{i}" for i in range(1, _EXPECTED_MINOR_ROWS + 1)],
)
def test_matrix_rows_have_applicability_and_status(row_id: str, matrix_rows: list[MatrixRow]) -> None:
    by_id = {row.row_id: row for row in matrix_rows}
    assert row_id in by_id, f"row {row_id!r} missing from the matrix; found {_row_ids(matrix_rows)}"
    row = by_id[row_id]
    assert row.applies_stdio, f"row {row_id} has an empty 'Applies to trw-mcp stdio' cell"
    assert row.applies_daemon, f"row {row_id} has an empty 'Applies to trw-memory loopback daemon' cell"
    assert row.status, f"row {row_id} has an empty Status cell"


#: FR03's own text: the three OAuth-hardening rows (RFC 9207 issuer, DCR/CIMD,
#: issuer-bound credentials) cite the FR03 determination in their daemon cell.
_FR03_DAEMON_MARKER = "(FR03)"
#: The exact trigger phrase FR03 requires be recorded next to the determination.
_FR03_TRIGGER_PHRASE = "beyond loopback"


def test_auth_rows_state_not_applicable_with_trigger(matrix_rows: list[MatrixRow], prd_markdown: str) -> None:
    """FR03: the OAuth-hardening rows read Not Applicable, with a stated re-evaluation trigger.

    The three rows are identified by their own citation of the FR03
    determination (``Not applicable (FR03)`` in the daemon-applicability
    cell), not by hardcoded row ids -- so a matrix edit that renumbers or
    reorders the Minor table doesn't silently stop checking the right rows.
    """
    auth_rows = [row for row in matrix_rows if _FR03_DAEMON_MARKER in row.applies_daemon]
    assert len(auth_rows) == 3, (
        f"expected 3 OAuth-hardening rows citing {_FR03_DAEMON_MARKER!r} in PRD-CORE-317's matrix "
        f"(RFC 9207 issuer, Dynamic Client Registration/CIMD, RFC 8707 resource indicators), "
        f"found {len(auth_rows)}: {[r.row_id for r in auth_rows]}"
    )
    for row in auth_rows:
        assert "not applicable" in row.status.lower(), (
            f"PRD-CORE-317 row {row.row_id} ({row.changelog_item!r}) must read Not Applicable, "
            f"got status {row.status!r}"
        )
        assert row.action.strip(), f"PRD-CORE-317 row {row.row_id} has an empty Action needed (reason) cell"

    assert _FR03_TRIGGER_PHRASE in prd_markdown, (
        f"PRD-CORE-317 must state the FR03 re-evaluation trigger ({_FR03_TRIGGER_PHRASE!r}: any daemon "
        f"transport binding beyond loopback) next to the OAuth-hardening determination"
    )


_PIN_RE_TEMPLATE = r'"({package}[<>=!~][^"]*)"'


def _find_dependency_pin(pyproject_path: Path, package: str) -> tuple[int, str]:
    """Return ``(1-indexed line number, exact constraint string)`` for *package*'s pin.

    Scans for a quoted dependency entry (``"mcp>=1.26.0"``), not a bare mention
    (e.g. the ``keywords = ["mcp", ...]`` list), by requiring a comparison
    operator immediately after the package name inside the quotes.
    """
    pattern = re.compile(_PIN_RE_TEMPLATE.format(package=re.escape(package)))
    for lineno, line in enumerate(pyproject_path.read_text(encoding="utf-8").splitlines(), start=1):
        match = pattern.search(line)
        if match:
            return lineno, match.group(1)
    raise AssertionError(f"no {package!r} dependency pin found in {pyproject_path}")


def test_matrix_cites_current_pin_lines(prd_markdown: str) -> None:
    """NFR02: the PRD's cited pyproject.toml lines and version constraints stay current.

    Parses the live pins rather than hardcoding line numbers, so this fails
    the moment either pin moves or its constraint changes without a matching
    PRD-CORE-317 update -- not just when the PRD happens to still match a
    number written down once.
    """
    assert MONOREPO_ROOT is not None  # narrowed by requires_monorepo skip
    mcp_line, mcp_constraint = _find_dependency_pin(Path(MONOREPO_ROOT) / "trw-mcp" / "pyproject.toml", "mcp")
    fastmcp_line, fastmcp_constraint = _find_dependency_pin(
        Path(MONOREPO_ROOT) / "trw-memory" / "pyproject.toml", "fastmcp"
    )

    mcp_citation = f"trw-mcp/pyproject.toml:{mcp_line}"
    fastmcp_citation = f"trw-memory/pyproject.toml:{fastmcp_line}"

    assert mcp_citation in prd_markdown, (
        f"PRD-CORE-317's NFR02 pin citation is stale: the live mcp pin is now at "
        f"{mcp_citation!r} but that line number is not cited in the PRD. Re-review and update "
        f"PRD-CORE-317 (docs/requirements-aare-f/prds/PRD-CORE-317.md) before trusting the matrix."
    )
    assert mcp_constraint in prd_markdown, (
        f"PRD-CORE-317's NFR02 pin citation is stale: the live mcp constraint is now "
        f"{mcp_constraint!r} but is not cited verbatim in the PRD. Re-review and update "
        f"PRD-CORE-317 (docs/requirements-aare-f/prds/PRD-CORE-317.md) before trusting the matrix."
    )
    assert fastmcp_citation in prd_markdown, (
        f"PRD-CORE-317's NFR02 pin citation is stale: the live fastmcp pin is now at "
        f"{fastmcp_citation!r} but that line number is not cited in the PRD. Re-review and update "
        f"PRD-CORE-317 (docs/requirements-aare-f/prds/PRD-CORE-317.md) before trusting the matrix."
    )
    assert fastmcp_constraint in prd_markdown, (
        f"PRD-CORE-317's NFR02 pin citation is stale: the live fastmcp constraint is now "
        f"{fastmcp_constraint!r} but is not cited verbatim in the PRD. Re-review and update "
        f"PRD-CORE-317 (docs/requirements-aare-f/prds/PRD-CORE-317.md) before trusting the matrix."
    )
