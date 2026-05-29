from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.code_index.discovery import discover_indexable_files
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
