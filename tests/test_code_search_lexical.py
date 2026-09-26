from __future__ import annotations

from pathlib import Path

from trw_mcp.code_index.search import lexical_search, symbol_search
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

    response = lexical_search(tmp_path, query="needle alpha", top_k=5)

    assert response.status == "ok"
    assert response.error_code == ""
    assert response.results[0].path == "src/alpha.py"
    assert response.results[0].symbol_name == "target_alpha"
    assert response.results[0].score > 0
    assert "lexical token match" in response.results[0].reason
    assert "needle alpha" in response.results[0].snippet
    assert len(response.results[0].snippet) <= 800


def test_every_build_chunks_the_current_files_and_drops_deleted_ones(tmp_path: Path) -> None:
    _write(tmp_path / "keep.py", "def keep() -> str:\n    return 'stable'\n")
    _write(tmp_path / "gone.py", "def gone() -> str:\n    return 'remove me'\n")
    first = update_code_index(tmp_path).chunk_stats

    (tmp_path / "gone.py").unlink()
    second = update_code_index(tmp_path).chunk_stats

    assert (first.indexed_files, second.indexed_files) == (2, 1)
    assert {hit.path for hit in lexical_search(tmp_path, query="stable remove").results} == {"keep.py"}


def test_a_store_built_by_an_older_chunker_is_rechunked_not_reused(tmp_path: Path) -> None:
    """C12 (7.0.0 rc2): unchanged files' rows were copied forward, so a chunker fix never reached an existing index."""
    import sqlite3

    from trw_mcp.code_index.store import default_store_path

    _write(
        tmp_path / "tool.py", "import os\nVALUE = 1\n\nif __name__ == '__main__':\n    launch_the_release_rocket()\n"
    )
    update_code_index(tmp_path)
    with sqlite3.connect(default_store_path(tmp_path)) as conn:  # what the pre-fix chunker left behind
        conn.execute("DELETE FROM chunks WHERE symbol_kind = 'module'")
        conn.execute("DELETE FROM meta WHERE key = 'chunk_format'")

    stats = update_code_index(tmp_path).chunk_stats

    assert stats.indexed_files == 1, "an older chunker's rows are never reused"
    assert [hit.path for hit in lexical_search(tmp_path, query="launch_the_release_rocket").results] == ["tool.py"]


def test_a_scoped_update_rebuilds_every_indexed_file_from_source(tmp_path: Path) -> None:
    """rc11 F2b: nothing is read back from the published store, so a scoped update of an older or missing store
    succeeds; an out-of-scope file changed or removed since its scan is left out until an unscoped update."""
    import sqlite3

    from trw_mcp.code_index.store import default_store_path

    _write(tmp_path / "gone.py", "def removed_later() -> None:\n    pass\n")
    _write(tmp_path / "kept.py", "def kept_out_of_scope() -> None:\n    pass\n")
    _write(tmp_path / "tool.py", "VALUE = 1\n\nif __name__ == '__main__':\n    launch_the_release_rocket()\n")
    update_code_index(tmp_path)
    with sqlite3.connect(default_store_path(tmp_path)) as conn:
        conn.execute("DELETE FROM meta WHERE key = 'chunk_format'")
    (tmp_path / "gone.py").unlink()
    _write(tmp_path / "kept.py", "def kept_out_of_scope_edited() -> None:\n    pass\n")

    scoped = update_code_index(tmp_path, paths=["tool.py"]).chunk_stats

    assert (scoped.indexed_files, scoped.failed_files) == (1, 2)
    assert [hit.path for hit in lexical_search(tmp_path, query="launch_the_release_rocket").results] == ["tool.py"]
    result = update_code_index(tmp_path)
    assert [row.path for row in result.manifest.files] == ["kept.py", "tool.py"]
    assert [hit.path for hit in lexical_search(tmp_path, query="kept_out_of_scope_edited").results] == ["kept.py"]
    default_store_path(tmp_path).unlink()
    assert update_code_index(tmp_path, paths=["tool.py"]).chunk_stats.indexed_files == 2


def test_symbol_search_prefers_exact_matches_before_fuzzy_matches(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "def target() -> str:\n    return 'exact'\n")
    _write(tmp_path / "b.py", "def target_extra() -> str:\n    return 'fuzzy'\n")
    update_code_index(tmp_path)

    response = symbol_search(tmp_path, symbol="target", top_k=5)

    assert response.status == "ok"
    assert response.results[0].symbol_name == "target"
    assert response.results[0].reason.startswith("exact symbol match")
    assert response.results[1].symbol_name == "target_extra"
