"""The code-index LIKE-scan bound compares TEXT, never a BLOB (code-index-blob review of PRD-CORE-316 FR05).

FR05 bounds each scanned column to its leading ``like_scan_max_bytes`` bytes with
``substr(CAST(col AS BLOB), 1, N)``. A SQLite compiled with ``SQLITE_LIKE_DOESNT_MATCH_BLOBS`` (a
documented option) returns false for every ``LIKE`` whose operand is a BLOB, which would empty every
search. The bound therefore casts the truncated bytes back to TEXT before ``LIKE``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from trw_mcp.code_index import search as search_module
from trw_mcp.code_index.bounds import CodeIndexBounds
from trw_mcp.code_index.search import lexical_search, symbol_search
from trw_mcp.code_index.store import CHUNK_COLUMNS, default_store_path
from trw_mcp.code_index.update import update_code_index

pytestmark = pytest.mark.integration

_SCANNED = ("symbol_name", "signature", "docstring_summary", "text", "path")
_ASCII = 'def ParseConfig() -> str:\n    """Load HelloWorld settings."""\n    return "HelloWorld"\n'
_MULTIBYTE = 'def café_naïve() -> str:\n    """日本語 docstring then AsciiTail."""\n    return "日本語 😀 résumé MultiByteNeedle"\n'


@pytest.fixture
def mixed_repo(tmp_path: Path) -> Path:
    (tmp_path / "ascii_mod.py").write_text(_ASCII, encoding="utf-8")
    (tmp_path / "uni_mod.py").write_text(_MULTIBYTE, encoding="utf-8")
    update_code_index(tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    ("kind", "query", "expected"),
    [
        ("lexical", "parseconfig", ["ascii_mod.py"]),  # (a) ASCII term in a different case
        ("lexical", "HELLOWORLD", ["ascii_mod.py"]),
        ("symbol", "PARSECONFIG", ["ascii_mod.py"]),
        ("lexical", "ParseConfig", ["ascii_mod.py"]),  # (b) exact term
        ("symbol", "ParseConfig", ["ascii_mod.py"]),
        ("symbol", "café_naïve", ["uni_mod.py"]),  # (c) multi-byte term
        ("symbol", "naïve", ["uni_mod.py"]),
        ("lexical", "multibyteneedle", ["uni_mod.py"]),  # ASCII term after multi-byte content in one row
        ("lexical", "absentterm", []),  # negative: a term in no file matches nothing
        ("symbol", "naïveabsent", []),
    ],
)
def test_the_served_search_matches_across_case_and_encoding(
    mixed_repo: Path, monkeypatch: pytest.MonkeyPatch, kind: str, query: str, expected: list[str]
) -> None:
    """The served path's hits, plus the SQL it ran: every bounded column is cast back to TEXT.

    The local SQLite builds are not compiled with ``SQLITE_LIKE_DOESNT_MATCH_BLOBS``, so no build
    available to this suite reproduces the BLOB-operand failure. Asserting on the executed WHERE
    clause is the only direct check possible without that compile flag.
    """
    wheres: list[str] = []
    real_stream_rows = search_module.stream_rows

    def recording(conn: sqlite3.Connection, *, where: str, params: tuple[object, ...]):  # type: ignore[no-untyped-def]
        wheres.append(where)
        return real_stream_rows(conn, where=where, params=params)

    monkeypatch.setattr(search_module, "stream_rows", recording)
    search = lexical_search if kind == "lexical" else symbol_search
    keyword = {"query": query} if kind == "lexical" else {"symbol": query}
    response = search(mixed_repo, **keyword)  # type: ignore[arg-type]

    assert response.status == "ok"
    assert sorted({hit.path for hit in response.results}) == expected
    [where] = wheres
    columns = _SCANNED if kind == "lexical" else ("symbol_name",)
    max_bytes = CodeIndexBounds().like_scan_max_bytes
    for column in columns:
        assert f"CAST(substr(CAST({column} AS BLOB), 1, {max_bytes}) AS TEXT) LIKE ?" in where, column
    assert f"AS BLOB), 1, {max_bytes}) LIKE" not in where  # no LIKE is left on a bare BLOB


def _insert_text_rows(root: Path, texts: dict[str, str]) -> None:
    """Add one chunk row per ``path -> text``, copied from the store's first row."""
    with sqlite3.connect(default_store_path(root)) as conn:
        row = list(conn.execute(f"SELECT {', '.join(CHUNK_COLUMNS)} FROM chunks").fetchone())
        rows = []
        for path, text in texts.items():
            copy = list(row)
            copy[CHUNK_COLUMNS.index("chunk_id")] = f"crafted:{path}"
            copy[CHUNK_COLUMNS.index("path")] = path
            copy[CHUNK_COLUMNS.index("text")] = text
            rows.append(tuple(copy))
        conn.executemany(
            f"INSERT INTO chunks ({', '.join(CHUNK_COLUMNS)}) VALUES ({', '.join('?' for _ in CHUNK_COLUMNS)})", rows
        )


def test_a_multibyte_row_longer_than_the_bound_is_truncated_not_dropped(tmp_path: Path) -> None:
    """A row over the byte bound, cut mid-character, still matches a term inside the bound.

    The term sits within the first 64 bytes. The bound's cut lands inside a 3-byte character, so the
    TEXT operand ends in a partial UTF-8 sequence. A term placed entirely past the bound stays
    unmatched, which is the documented FR05 tradeoff.
    """
    (tmp_path / "a.py").write_text("def f() -> None:\n    return None\n", encoding="utf-8")
    update_code_index(tmp_path)
    head = "é" * 20 + " edgemarker  "  # 40 + 13 = 53 bytes
    _insert_text_rows(
        tmp_path,
        {
            "inside.py": head + "日" * 1000,  # byte 64 falls inside the 5th 3-byte character
            "outside.py": "日" * 30 + " edgemarker",  # the term starts at byte 91
        },
    )
    bounds = CodeIndexBounds(like_scan_max_bytes=64)
    assert len((head + "日" * 1000).encode()) > 64 and (64 - len(head.encode())) % 3 != 0

    response = lexical_search(tmp_path, query="edgemarker", bounds=bounds)

    assert response.status == "ok"
    assert [hit.path for hit in response.results] == ["inside.py"]
    assert lexical_search(tmp_path, query="EDGEMARKER", bounds=bounds).results[0].path == "inside.py"
