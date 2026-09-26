"""PRD-SEC-015 round-2 audit, Row 2: code search and symbol lookup never write
the code index under the reviewer role.

Row 2 was a reviewer-only branch: every other query reconciled and rewrote
chunks.json. PRD-CORE-300-FR15 makes every query read-only, so the reviewer
guarantee now holds for all roles; these tests keep it pinned for the role
that must never write.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.code_index.search import lexical_search, symbol_search
from trw_mcp.code_index.store import default_store_path
from trw_mcp.code_index.update import update_code_index
from trw_mcp.state import _surface_role


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    yield
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    _surface_role.reset_surface_role_state()


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


@pytest.mark.parametrize("role", ["reviewer", ""])
def test_a_query_without_a_store_never_creates_the_code_index_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    monkeypatch.setenv("TRW_SURFACE_ROLE", role)

    for response in (lexical_search(tmp_path, query="anything"), symbol_search(tmp_path, symbol="anything")):
        assert response.status == "failed"
        assert response.error_code == "index_missing"
    assert not default_store_path(tmp_path).parent.exists()


def test_reviewer_search_serves_the_published_store_without_reconciling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer reads the store exactly as built: a file added afterward is
    absent, and the store's bytes are unchanged."""
    _write(tmp_path / "src" / "alpha.py", 'def target_alpha() -> str:\n    return "needle"\n')
    update_code_index(tmp_path)
    store = default_store_path(tmp_path)
    before_bytes = store.read_bytes()
    _write(tmp_path / "src" / "beta.py", 'def target_beta() -> str:\n    return "needle"\n')

    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    response = lexical_search(tmp_path, query="needle")

    assert response.status == "ok"
    assert response.index_revision
    assert {hit.path for hit in response.results} == {"src/alpha.py"}
    assert store.read_bytes() == before_bytes, "a reviewer query must never rewrite the store"
