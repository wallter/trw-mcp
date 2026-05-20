from __future__ import annotations

from pathlib import Path

from trw_mcp.code_index.search import lexical_search, symbol_search, update_chunk_index
from trw_mcp.code_index.update import update_code_index


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_lexical_search_ranks_chunks_and_caps_privacy_safe_snippets(tmp_path: Path) -> None:
    _write(
        tmp_path / "src" / "alpha.py",
        'def target_alpha() -> str:\n    """alpha doc"""\n    return "needle alpha"\n',
    )
    _write(tmp_path / "src" / "beta.py", "def beta() -> str:\n    return 'other'\n")
    update_code_index(tmp_path)
    update_chunk_index(tmp_path)

    response = lexical_search(tmp_path, query="needle alpha", top_k=5)

    assert response.status == "ok"
    assert response.error_code == ""
    assert response.results[0].path == "src/alpha.py"
    assert response.results[0].symbol_name == "target_alpha"
    assert response.results[0].score > 0
    assert "lexical token match" in response.results[0].reason
    assert "needle alpha" in response.results[0].snippet
    assert len(response.results[0].snippet) <= 800


def test_chunk_index_skips_unchanged_files_and_removes_deleted_chunks(tmp_path: Path) -> None:
    _write(tmp_path / "keep.py", "def keep() -> str:\n    return 'stable'\n")
    _write(tmp_path / "gone.py", "def gone() -> str:\n    return 'remove me'\n")
    update_code_index(tmp_path)
    first = update_chunk_index(tmp_path)

    (tmp_path / "gone.py").unlink()
    update_code_index(tmp_path)
    second = update_chunk_index(tmp_path)

    assert first.stats.added_files == 2
    assert second.stats.unchanged_files == 1
    assert second.stats.deleted_files == 1
    assert {chunk.path for chunk in second.index.chunks} == {"keep.py"}


def test_symbol_search_prefers_exact_matches_before_fuzzy_matches(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "def target() -> str:\n    return 'exact'\n")
    _write(tmp_path / "b.py", "def target_extra() -> str:\n    return 'fuzzy'\n")
    update_code_index(tmp_path)
    update_chunk_index(tmp_path)

    response = symbol_search(tmp_path, symbol="target", top_k=5)

    assert response.status == "ok"
    assert response.results[0].symbol_name == "target"
    assert response.results[0].reason.startswith("exact symbol match")
    assert response.results[1].symbol_name == "target_extra"
