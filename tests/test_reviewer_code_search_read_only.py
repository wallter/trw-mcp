"""PRD-SEC-015 round-2 audit, Row 2: trw_code_search/trw_code_symbol must
never write ``.trw/code-index/chunks.json`` under the reviewer role.

RED-FIRST: with the ``reviewer_role_active()`` branch in
``code_index/search.py::_resolve_index_for_read`` reverted (unconditional
``update_chunk_index``), every test in this file that asserts "no write"
fails, because ``update_chunk_index`` always calls ``save_chunk_index``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.code_index.search import default_chunk_index_path, lexical_search, symbol_search
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


def test_reviewer_lexical_search_never_creates_the_code_index_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No manifest, no prior index: the reviewer role must get a clean
    ``missing_index`` failure and MUST NOT create ``.trw/code-index/``."""
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")

    response = lexical_search(tmp_path, query="anything")

    assert response.status == "failed"
    assert response.error_code == "missing_index"
    assert not default_chunk_index_path(tmp_path).parent.exists()


def test_reviewer_symbol_search_never_creates_the_code_index_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")

    response = symbol_search(tmp_path, symbol="anything")

    assert response.status == "failed"
    assert response.error_code == "missing_index"
    assert not default_chunk_index_path(tmp_path).parent.exists()


def test_reviewer_search_serves_an_existing_stale_index_without_reconciling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An index built as an agent stays exactly as-is: the reviewer role reads
    it (index_state="read_only_stale") but a NEW file added afterward never
    shows up, because reconciliation never ran."""
    _write(tmp_path / "src" / "alpha.py", 'def target_alpha() -> str:\n    return "needle"\n')
    update_code_index(tmp_path)
    from trw_mcp.code_index.search import update_chunk_index

    update_chunk_index(tmp_path)
    index_path = default_chunk_index_path(tmp_path)
    before_bytes = index_path.read_bytes()

    # A file added AFTER the index was built, as an ordinary agent session.
    _write(tmp_path / "src" / "beta.py", 'def target_beta() -> str:\n    return "needle"\n')
    update_code_index(tmp_path)  # refreshes only the PRD-CORE-171 manifest, not chunks.json

    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    response = lexical_search(tmp_path, query="needle")

    assert response.status == "ok"
    assert response.index_state == "read_only_stale"
    # The new file's chunk is absent -- reconciliation never ran.
    assert {hit.path for hit in response.results} == {"src/alpha.py"}
    assert index_path.read_bytes() == before_bytes, "reviewer search must never rewrite chunks.json"


def test_agent_role_index_state_is_the_empty_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-vacuity / regression: an ordinary agent session still reconciles
    and reports the empty (non-stale) ``index_state``."""
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    _write(tmp_path / "src" / "alpha.py", 'def target_alpha() -> str:\n    return "needle"\n')
    update_code_index(tmp_path)

    response = lexical_search(tmp_path, query="needle")

    assert response.status == "ok"
    assert response.index_state == ""
    assert default_chunk_index_path(tmp_path).exists()
