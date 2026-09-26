from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.code_index.bounds import CodeIndexBounds, IndexBoundExceeded
from trw_mcp.code_index.discovery import discover_indexable_files
from trw_mcp.code_index.models import CodeIndexFileRow
from trw_mcp.code_index.search import lexical_search
from trw_mcp.code_index.storage import default_manifest_path, load_manifest, save_manifest
from trw_mcp.code_index.update import update_code_index


def _write(path: Path, body: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(body, bytes):
        path.write_bytes(body)
    else:
        path.write_text(body, encoding="utf-8")


def test_discovery_skips_default_excludes_binary_and_oversized_files(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "app.py", "print('ok')\n")
    _write(tmp_path / ".git" / "config", "secret-ish but excluded\n")
    _write(tmp_path / ".trw" / "state.json", "{}\n")
    _write(tmp_path / "node_modules" / "dep.js", "dep\n")
    _write(tmp_path / ".venv" / "lib.py", "venv\n")
    _write(tmp_path / "build" / "bundle.py", "generated\n")
    _write(tmp_path / "src" / "blob.py", b"abc\x00def")
    _write(tmp_path / "src" / "large.py", "x" * 40)
    _write(tmp_path / "src" / "ignored.txt", "not included\n")

    result = discover_indexable_files(
        tmp_path,
        max_file_bytes=20,
        include_extensions=frozenset({".py"}),
    )

    assert [path.relative_to(tmp_path).as_posix() for path in result.files] == ["src/app.py"]
    assert result.skipped_count == 7


def test_update_classifies_added_unchanged_modified_and_deleted_by_sha256(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "one\n")
    _write(tmp_path / "b.py", "two\n")

    first = update_code_index(tmp_path)

    assert first.stats.added == 2
    assert first.stats.total_files == 2
    first_hashes = {row.path: row.sha256 for row in first.manifest.files}

    _write(tmp_path / "a.py", "one\n")
    _write(tmp_path / "b.py", "changed\n")
    _write(tmp_path / "c.py", "three\n")
    (tmp_path / "a.py").unlink()

    second = update_code_index(tmp_path)

    assert second.stats.added == 1
    assert second.stats.unchanged == 0
    assert second.stats.modified == 1
    assert second.stats.deleted == 1
    assert {row.path for row in second.manifest.files} == {"b.py", "c.py"}
    second_hashes = {row.path: row.sha256 for row in second.manifest.files}
    assert second_hashes["b.py"] != first_hashes["b.py"]


def test_update_counts_unchanged_and_path_limits_without_deleting_out_of_scope_rows(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "one\n")
    _write(tmp_path / "pkg" / "b.py", "two\n")
    initial = update_code_index(tmp_path)

    _write(tmp_path / "pkg" / "b.py", "changed\n")
    limited = update_code_index(tmp_path, paths=["pkg"])

    assert limited.stats.added == 0
    assert limited.stats.modified == 1
    assert limited.stats.deleted == 0
    assert {row.path for row in limited.manifest.files} == {"a.py", "pkg/b.py"}
    assert {row.path for row in initial.manifest.files} == {"a.py", "pkg/b.py"}


def test_scoped_update_keeps_edited_and_drops_deleted_out_of_scope_files(tmp_path: Path) -> None:
    """B71-108: a scoped update re-chunks an out-of-scope file edited since its row, and drops a vanished one."""
    _write(tmp_path / "a.py", "def alpha_first() -> None:\n    pass\n")
    _write(tmp_path / "b.py", "def beta_first() -> None:\n    pass\n")
    _write(tmp_path / "c.py", "def gamma_first() -> None:\n    pass\n")
    first = update_code_index(tmp_path)

    _write(tmp_path / "b.py", "def beta_edited() -> None:\n    pass\n")
    (tmp_path / "c.py").unlink()
    scoped = update_code_index(tmp_path, paths=["a.py"])

    assert (scoped.stats.added, scoped.stats.unchanged, scoped.stats.modified, scoped.stats.deleted) == (0, 1, 1, 1)
    assert (scoped.chunk_stats.indexed_files, scoped.chunk_stats.failed_files) == (2, 0)
    rows = {row.path: row for row in scoped.manifest.files}
    assert set(rows) == {"a.py", "b.py"}
    assert rows["b.py"].sha256 != {row.path: row for row in first.manifest.files}["b.py"].sha256
    assert [hit.path for hit in lexical_search(tmp_path, query="beta_edited").results] == ["b.py"]
    assert not lexical_search(tmp_path, query="beta_first").results


def test_scoped_update_refilters_out_of_scope_manifest_rows(tmp_path: Path) -> None:
    """The manifest is the checkout's: a crafted row for an excluded file stays out of the store (B71-108 review)."""
    _write(tmp_path / "a.py", "def alpha() -> None:\n    pass\n")
    _write(tmp_path / "node_modules" / "vendored.py", "def vendored_secret() -> None:\n    pass\n")
    first = update_code_index(tmp_path)
    assert [row.path for row in first.manifest.files] == ["a.py"]
    crafted = CodeIndexFileRow(
        path="node_modules/vendored.py", sha256="0" * 64, size_bytes=1, indexed_at=first.manifest.generated_at
    )
    save_manifest(
        default_manifest_path(tmp_path), first.manifest.model_copy(update={"files": [*first.manifest.files, crafted]})
    )

    scoped = update_code_index(tmp_path, paths=["a.py"])

    assert [row.path for row in scoped.manifest.files] == ["a.py"]
    assert scoped.stats.deleted == 1
    assert not lexical_search(tmp_path, query="vendored_secret").results


def test_scoped_update_never_walks_a_manifest_row_that_names_a_directory(tmp_path: Path) -> None:
    """A row whose file became a directory, or a crafted ".", must not widen the scope (B71-108 sol r2)."""
    _write(tmp_path / "a.py", "def alpha() -> None:\n    pass\n")
    _write(tmp_path / "old.py", "def old() -> None:\n    pass\n")
    first = update_code_index(tmp_path)
    crafted = CodeIndexFileRow(path=".", sha256="0" * 64, size_bytes=1, indexed_at=first.manifest.generated_at)
    save_manifest(
        default_manifest_path(tmp_path), first.manifest.model_copy(update={"files": [*first.manifest.files, crafted]})
    )
    (tmp_path / "old.py").unlink()
    _write(tmp_path / "old.py" / "inside.py", "def inside_new_dir() -> None:\n    pass\n")
    _write(tmp_path / "elsewhere.py", "def elsewhere_new() -> None:\n    pass\n")

    scoped = update_code_index(tmp_path, paths=["a.py"])

    assert [row.path for row in scoped.manifest.files] == ["a.py"]
    assert scoped.stats.deleted == 2
    assert not lexical_search(tmp_path, query="inside_new_dir").results
    assert not lexical_search(tmp_path, query="elsewhere_new").results


def test_discovery_never_walks_a_file_only_limit(tmp_path: Path) -> None:
    """A manifest row is judged a file where discovery reads it, so a directory swapped in late is not walked."""
    _write(tmp_path / "a.py", "one\n")
    _write(tmp_path / "was_a_file.py" / "inside.py", "two\n")

    found = discover_indexable_files(tmp_path, paths=["a.py"], files=["was_a_file.py", ".", "a.py"])

    assert [path.relative_to(tmp_path.resolve()).as_posix() for path in found.files] == ["a.py"]


def test_scoped_update_rediscovers_manifest_rows_verbatim(tmp_path: Path) -> None:
    """A manifest path is not a scope input: " a.py" stays " a.py", and "a.py" is not indexed in its place (sol r4)."""
    _write(tmp_path / " a.py", "def spaced() -> None:\n    pass\n")
    _write(tmp_path / "b.py", "two\n")
    update_code_index(tmp_path)
    _write(tmp_path / "a.py", "def unspaced_out_of_scope() -> None:\n    pass\n")

    scoped = update_code_index(tmp_path, paths=["b.py"])

    assert [row.path for row in scoped.manifest.files] == [" a.py", "b.py"]
    assert not lexical_search(tmp_path, query="unspaced_out_of_scope").results


def test_discovery_charges_missing_file_only_limits_to_the_entry_budget(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "one\n")

    with pytest.raises(IndexBoundExceeded, match="build_max_entries"):
        discover_indexable_files(
            tmp_path,
            paths=["a.py"],
            files=[f"gone{n}.py" for n in range(10)],
            bounds=CodeIndexBounds(build_max_entries=5),
        )


def test_scoped_update_charges_out_of_scope_rows_to_the_build_budgets(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "one\n")
    _write(tmp_path / "b.py", "two\n")
    update_code_index(tmp_path)

    with pytest.raises(IndexBoundExceeded, match="build_max_files"):
        update_code_index(tmp_path, paths=["a.py"], bounds=CodeIndexBounds(build_max_files=1))


def test_update_normalizes_dot_path_limits_without_duplicate_rows(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "one\n")
    _write(tmp_path / "pkg" / "b.py", "two\n")
    update_code_index(tmp_path)

    _write(tmp_path / "pkg" / "b.py", "changed\n")
    dot_limited = update_code_index(tmp_path, paths=["./pkg"])
    repo_limited = update_code_index(tmp_path, paths=["."])

    assert dot_limited.stats.modified == 1
    assert [row.path for row in dot_limited.manifest.files] == ["a.py", "pkg/b.py"]
    assert repo_limited.stats.unchanged == 2
    assert [row.path for row in repo_limited.manifest.files] == ["a.py", "pkg/b.py"]


def test_update_accepts_single_pass_path_iterables(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "one\n")
    _write(tmp_path / "pkg" / "b.py", "two\n")
    update_code_index(tmp_path)

    _write(tmp_path / "pkg" / "b.py", "changed\n")
    limited = update_code_index(tmp_path, paths=(path for path in ["pkg"]))

    assert limited.stats.modified == 1
    assert [row.path for row in limited.manifest.files] == ["a.py", "pkg/b.py"]


def test_missing_or_corrupt_manifest_rebuilds_safely(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "one\n")
    first = update_code_index(tmp_path)
    manifest_path = default_manifest_path(tmp_path)
    manifest_path.write_text("{not-json", encoding="utf-8")

    rebuilt = update_code_index(tmp_path)

    assert rebuilt.stats.added == 1
    assert rebuilt.stats.modified == 0
    assert rebuilt.stats.deleted == 0
    assert load_manifest(manifest_path) is not None
    assert manifest_path.read_text(encoding="utf-8") != first.manifest.model_dump_json()


def test_save_manifest_uses_atomic_replace_and_preserves_previous_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write(tmp_path / "a.py", "one\n")
    result = update_code_index(tmp_path)
    manifest_path = default_manifest_path(tmp_path)
    original = manifest_path.read_text(encoding="utf-8")

    def fail_replace(src: str | bytes | Path, dst: str | bytes | Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("trw_mcp.code_index.storage.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        save_manifest(manifest_path, result.manifest)

    assert manifest_path.read_text(encoding="utf-8") == original
    assert json.loads(original)["schema_version"] == "code-index-manifest/v1"
