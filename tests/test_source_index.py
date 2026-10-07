"""The shared source index (``tests/_source_index.py``) keeps every census honest (P2c, PLAN section 6).

A cache that served a stale or mutated tree would turn many censuses green at once, so this file pins the
properties the migration relies on: content-hash keys, re-parse after an edit, option keys, private copies for
mutators, no caching of failures, and parity with a fresh ``ast.parse`` over the whole of ``trw_mcp``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import trw_mcp
from tests import _source_index as source_index

_SRC = Path(trw_mcp.__file__).resolve().parent


@pytest.fixture(autouse=True)
def _isolated_index() -> None:
    """Each test sees a cold index, and leaves none of its entries behind for another test's counters."""
    source_index.clear()


def test_a_second_lookup_of_an_unchanged_file_is_a_hit_on_the_same_tree(tmp_path: Path) -> None:
    path = tmp_path / "mod.py"
    path.write_text("x = 1\n", encoding="utf-8")
    first = source_index.tree(path)
    second = source_index.tree(path)
    assert second is first
    assert source_index.stats()["misses"] == 1
    assert source_index.stats()["hits"] == 1


def test_a_file_edited_after_it_was_indexed_is_reread_and_reparsed(tmp_path: Path) -> None:
    path = tmp_path / "mod.py"
    path.write_text("def old():\n    return 1\n", encoding="utf-8")
    before = source_index.source(path)
    assert [n.name for n in before.tree.body if isinstance(n, ast.FunctionDef)] == ["old"]

    path.write_text("def new():\n    return 2\n", encoding="utf-8")
    after = source_index.source(path)

    assert after.sha256 != before.sha256
    assert after.text == "def new():\n    return 2\n"
    assert [n.name for n in after.tree.body if isinstance(n, ast.FunctionDef)] == ["new"]
    assert source_index.stats()["misses"] == 2, "the edit must cost a fresh parse"
    # Same size, same mtime-resolution-sized edit: only the content hash tells these apart.
    path.write_text("def neu():\n    return 2\n", encoding="utf-8")
    assert [n.name for n in source_index.tree(path).body if isinstance(n, ast.FunctionDef)] == ["neu"]


def test_a_planted_copy_shares_unchanged_files_and_reparses_only_the_planted_one(tmp_path: Path) -> None:
    real = _SRC / "_checkout_access.py"
    clean = tmp_path / "clean.py"
    clean.write_text(real.read_text(encoding="utf-8"), encoding="utf-8")
    planted = tmp_path / "planted.py"
    planted.write_text(real.read_text(encoding="utf-8") + "\n\ndef _planted():\n    return 1\n", encoding="utf-8")

    real_tree = source_index.tree(real)
    assert source_index.tree(clean) is real_tree, "an identical copy hits the entry by content hash"
    planted_tree = source_index.tree(planted)
    assert planted_tree is not real_tree
    names = {n.name for n in ast.walk(planted_tree) if isinstance(n, ast.FunctionDef)}
    assert "_planted" in names
    assert "_planted" not in {n.name for n in ast.walk(real_tree) if isinstance(n, ast.FunctionDef)}


def test_parse_options_are_part_of_the_key() -> None:
    source = "value = 1  # type: int\n"
    plain = source_index.parse(source)
    typed = source_index.parse(source, type_comments=True)
    assert typed is not plain
    assert plain.body[0].type_comment is None
    assert typed.body[0].type_comment == "int"
    assert source_index.parse(source, type_comments=True) is typed
    old = source_index.parse("match x:\n    case 1:\n        pass\n")
    with pytest.raises(SyntaxError):
        source_index.parse("match x:\n    case 1:\n        pass\n", feature_version=(3, 8))
    assert isinstance(old.body[0], ast.Match)


def test_a_mutator_takes_a_private_copy_and_the_shared_tree_is_untouched(tmp_path: Path) -> None:
    path = tmp_path / "mod.py"
    path.write_text("x = 1\n", encoding="utf-8")
    shared = source_index.tree(path)
    private = source_index.tree_copy(path)
    assert private is not shared
    private.body.clear()
    assert len(source_index.tree(path).body) == 1
    assert source_index.parse_copy("y = 2\n") is not source_index.parse("y = 2\n")


def test_a_syntax_error_is_never_cached(tmp_path: Path) -> None:
    path = tmp_path / "bad.py"
    path.write_text("def (:\n", encoding="utf-8")
    for _ in range(2):
        with pytest.raises(SyntaxError):
            source_index.tree(path)
    assert source_index.stats()["trees"] == 0
    path.write_text("def ok():\n    pass\n", encoding="utf-8")
    assert isinstance(source_index.tree(path), ast.Module)


def test_bypass_stores_nothing_and_hands_back_fresh_trees(tmp_path: Path) -> None:
    path = tmp_path / "mod.py"
    path.write_text("x = 1\n", encoding="utf-8")
    with source_index.bypass():
        first = source_index.tree(path)
        second = source_index.tree(path)
        assert source_index.read(path) == "x = 1\n"
    assert first is not second
    assert source_index.stats() == {"hits": 0, "misses": 0, "texts": 0, "trees": 0}


def test_the_environment_switch_bypasses_the_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "mod.py"
    path.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setenv("TRW_SOURCE_INDEX", "off")
    assert source_index.tree(path) is not source_index.tree(path)
    assert source_index.stats()["trees"] == 0


def test_text_matches_path_read_text_including_universal_newlines(tmp_path: Path) -> None:
    path = tmp_path / "crlf.py"
    path.write_bytes(b"a = 1\r\nb = 2\rc = 3\n")
    assert source_index.read(path) == path.read_text(encoding="utf-8")
    assert source_index.source(path).text == path.read_text(encoding="utf-8")


def test_the_ast_proxy_caches_parse_and_passes_every_other_name_through() -> None:
    proxy = source_index.cached_ast
    assert proxy.parse("z = 3\n") is proxy.parse("z = 3\n")
    assert proxy.Call is ast.Call
    assert proxy.walk is ast.walk


def test_every_cached_tree_of_trw_mcp_equals_a_fresh_parse() -> None:
    """Parity over the whole package: the index never changes what a scanner sees (an uncached full walk)."""
    files = source_index.py_files(_SRC)
    assert len(files) > 500, "the walk found no package; the parity check would pass vacuously"
    cached = {path: ast.dump(source_index.tree(path), include_attributes=True) for path in files}
    with source_index.bypass():
        for path in files:
            fresh = ast.dump(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)), include_attributes=True)
            assert cached[path] == fresh, f"{path} differs between the cached and a fresh parse"
