"""The code index is safe before trw_code ships (PRD-CORE-300-FR15, S10-pre).

On 2026-09-24 one update walked 268,947 files under nested worktrees, wrote an
8.4 GB chunks.json, and a query that loaded it whole took the MCP server to
62 GB. These tests pin the four fixes: a walk that prunes nested checkouts, a
store that only the build writes, build and query budgets that fail by name,
and a query whose peak memory stays flat on a store ten times its row budget.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from trw_mcp.code_index import discovery as discovery_module
from trw_mcp.code_index.bounds import MAX_INDEXED_FILE_BYTES, CodeIndexBounds, Deadline, IndexBoundExceeded
from trw_mcp.code_index.discovery import discover_indexable_files
from trw_mcp.code_index.search import lexical_search, symbol_search
from trw_mcp.code_index.store import CHUNK_COLUMNS, default_store_path
from trw_mcp.code_index.update import update_code_index

pytestmark = pytest.mark.unit


def _write(path: Path, body: str = "def f() -> None:\n    return None\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _indexed(root: Path, **kwargs: object) -> set[str]:
    result = discover_indexable_files(root, **kwargs)  # type: ignore[arg-type]
    return {path.relative_to(root.resolve()).as_posix() for path in result.files}


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _store_fingerprint(root: Path) -> tuple[str, int]:
    path = default_store_path(root)
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns


@pytest.fixture
def nested_repo(tmp_path: Path) -> Path:
    """A root that is itself a linked worktree, holding every kind of nested checkout."""
    root = tmp_path / "repo"
    _write(root / ".git", "gitdir: /elsewhere/.git/worktrees/repo\n")
    _write(root / "src" / "app.py")
    _write(root / ".claude" / "worktrees" / "lane" / ".git", "gitdir: /elsewhere\n")
    _write(root / ".claude" / "worktrees" / "lane" / "src" / "copy.py")
    _write(root / ".claude" / "settings.py")
    _write(root / "vendor" / "clone" / ".git" / "HEAD", "ref: refs/heads/main\n")
    _write(root / "vendor" / "clone" / "lib.py")
    _write(root / "third_party" / "sub" / ".git", "gitdir: ../../.git/modules/sub\n")
    _write(root / "third_party" / "sub" / "mod.py")
    _write(root / "odd" / ".git", "")
    _write(root / "odd" / "junk.py")
    _write(root / ".trw" / "state.py")
    outside = tmp_path / "outside"
    _write(outside / "leak.py")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    return root


# --- traversal ---------------------------------------------------------------


def test_the_root_stays_eligible_and_every_nested_checkout_is_pruned(nested_repo: Path) -> None:
    assert _indexed(nested_repo) == {"src/app.py", ".claude/settings.py"}


def test_the_walk_never_enters_a_pruned_directory(nested_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entered: list[str] = []
    real_scandir = os.scandir

    def spy(path: str | os.PathLike[str] = ".") -> object:
        entered.append(Path(path).as_posix())
        return real_scandir(path)

    with monkeypatch.context() as patch:
        patch.setattr(discovery_module.os, "scandir", spy)
        discover_indexable_files(nested_repo)

    assert entered, "the spy saw no walk"

    pruned = (".claude/worktrees", "vendor/clone", "third_party/sub", "odd", ".trw", "linked")
    for name in pruned:
        assert not any(p.endswith(f"/{name}") or f"/{name}/" in p for p in entered), name


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 directory")
def test_an_unreadable_directory_is_skipped_and_counted(tmp_path: Path) -> None:
    _write(tmp_path / "ok.py")
    locked = tmp_path / "locked"
    _write(locked / "hidden.py")
    locked.chmod(0)
    try:
        result = discover_indexable_files(tmp_path)
    finally:
        locked.chmod(0o755)

    assert [p.name for p in result.files] == ["ok.py"]
    assert result.unreadable_dirs == 1


def test_an_explicit_path_filter_cannot_reenter_a_pruned_directory(nested_repo: Path) -> None:
    for limit in ("vendor/clone", ".claude/worktrees/lane/src", "odd/junk.py", "linked", ".trw"):
        assert _indexed(nested_repo, paths=[limit]) == set(), limit
    assert _indexed(nested_repo, paths=["src"]) == {"src/app.py"}


# --- build budgets -----------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("build_max_entries", 2),
        ("build_max_files", 2),
        ("build_max_source_bytes", 60),
        ("build_timeout_seconds", 1e-9),
    ],
)
def test_each_build_budget_stops_the_build_by_name(tmp_path: Path, field: str, limit: float) -> None:
    for name in ("a.py", "b.py", "c.py"):
        _write(tmp_path / name)

    with pytest.raises(IndexBoundExceeded) as caught:
        update_code_index(tmp_path, bounds=CodeIndexBounds(**{field: limit}))

    assert caught.value.bound == f"code_index_bounds.{field}"


class _CountingScandir:
    """Wraps os.scandir and counts the entries the walk actually pulls from it."""

    def __init__(self) -> None:
        self.consumed = 0
        self._real = os.scandir

    def __call__(self, path: str | os.PathLike[str] = ".") -> object:
        counter = self

        class _Listing:
            def __enter__(self) -> _Listing:
                self._inner = counter._real(path)
                self._it = self._inner.__enter__()
                return self

            def __exit__(self, *exc: object) -> None:
                self._inner.__exit__(*exc)

            def __iter__(self) -> _Listing:
                return self

            def __next__(self) -> os.DirEntry[str]:
                entry = next(self._it)
                counter.consumed += 1
                return entry

        return _Listing()


class _TripAfter(Deadline):
    """A deadline that expires on its Nth check, independent of the clock."""

    def __init__(self, checks: int) -> None:
        super().__init__(3600, "build_timeout_seconds")
        self.calls = 0
        self._checks = checks

    def expired(self) -> bool:
        self.calls += 1
        return self.calls >= self._checks


def test_the_entry_cap_stops_scandir_before_the_directory_is_materialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for i in range(200):
        _write(tmp_path / f"f{i:03}.py")
    counting = _CountingScandir()
    monkeypatch.setattr(discovery_module.os, "scandir", counting)

    with pytest.raises(IndexBoundExceeded) as caught:
        discover_indexable_files(tmp_path, bounds=CodeIndexBounds(build_max_entries=1))

    assert caught.value.bound == "code_index_bounds.build_max_entries"
    assert counting.consumed == 2, "the cap must trip on the first entry past it, not after listing all 200"


def test_the_deadline_is_checked_during_enumeration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for i in range(200):
        _write(tmp_path / f"f{i:03}.py")
    counting = _CountingScandir()
    monkeypatch.setattr(discovery_module.os, "scandir", counting)
    # Check 1 is the directory boundary; checks 2 and 3 are the first two entries.
    deadline = _TripAfter(3)

    with pytest.raises(IndexBoundExceeded) as caught:
        discover_indexable_files(tmp_path, deadline=deadline)

    assert caught.value.bound == "code_index_bounds.build_timeout_seconds"
    assert counting.consumed == 2


def test_a_failed_build_leaves_the_previous_store_answering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path / "a.py", "def alpha_target() -> None:\n    return None\n")
    update_code_index(tmp_path)
    before = _store_fingerprint(tmp_path)

    _write(tmp_path / "b.py", "def beta() -> None:\n    return None\n")
    from trw_mcp.code_index import store as store_module

    def interrupted(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        raise KeyboardInterrupt

    monkeypatch.setattr(store_module, "chunk_source", interrupted)
    with pytest.raises(KeyboardInterrupt):
        update_code_index(tmp_path)

    assert _store_fingerprint(tmp_path) == before
    assert [p.name for p in default_store_path(tmp_path).parent.iterdir() if p.name.endswith(".tmp")] == []
    assert symbol_search(tmp_path, symbol="alpha_target").results[0].path == "a.py"


# --- query budgets -----------------------------------------------------------


def test_each_query_budget_fails_by_name_and_the_next_query_answers(tmp_path: Path) -> None:
    for index in range(5):
        _write(tmp_path / f"m{index}.py", f"def needle_{index}() -> str:\n    return 'needle haystack'\n")
    update_code_index(tmp_path)

    cases = {
        "query_max_rows": CodeIndexBounds(query_max_rows=4),
        "query_max_response_bytes": CodeIndexBounds(query_max_response_bytes=200),
        "query_timeout_seconds": CodeIndexBounds(query_timeout_seconds=1e-9),
    }
    for field, bounds in cases.items():
        response = lexical_search(tmp_path, query="needle", bounds=bounds)
        assert (response.status, response.error_code, response.bound) == (
            "failed",
            "index_bound_exceeded",
            f"code_index_bounds.{field}",
        ), field

    assert len(lexical_search(tmp_path, query="needle").results) == 5


def test_an_oversized_query_is_refused_by_name_before_the_store_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scan costs rows x distinct terms, one LIKE each: both are capped as in trw-memory's FTS leg (rc7 sweep)."""
    _write(tmp_path / "a.py", "def needle() -> None:\n    return None\n")
    update_code_index(tmp_path)
    terms = [f"t{index}" for index in range(65)]
    with monkeypatch.context() as patch:
        patch.setattr("trw_mcp.code_index.search.open_store", lambda *_a: pytest.fail("the store was opened"))
        cases = {
            "query_max_chars": lexical_search(tmp_path, query="needle " + "x" * 1_000_000),
            "query_max_terms": lexical_search(tmp_path, query=" ".join(terms)),
            "query_max_chars ": symbol_search(tmp_path, symbol="n" * 1_001),
        }
    for field, response in cases.items():
        assert (response.error_code, response.bound) == ("index_bound_exceeded", f"code_index_bounds.{field.strip()}")
        assert len(response.query) <= 1_000  # the refusal echoes no more than the cap

    assert lexical_search(tmp_path, query=" ".join(["needle", *terms[:63]])).results[0].path == "a.py"


# --- the build reopens a discovered path (rc8 pre-C12 sol review) -----------


def _after_discovery(monkeypatch: pytest.MonkeyPatch, mutate: object) -> None:
    """Run *mutate* between the walk that sized the files and the build that reads them."""
    from trw_mcp.code_index import update as update_module

    real = update_module.discover_indexable_files

    def discover_then_mutate(*args: object, **kwargs: object) -> object:
        result = real(*args, **kwargs)  # type: ignore[arg-type]
        mutate()  # type: ignore[operator]
        return result

    monkeypatch.setattr(update_module, "discover_indexable_files", discover_then_mutate)


@pytest.mark.timeout(60)
@pytest.mark.parametrize("swap", ["directory-symlink", "file-symlink", "in-root-symlink", "fifo", "grown"])
def test_a_path_swapped_after_the_walk_is_never_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, swap: str
) -> None:
    root = tmp_path / "repo"
    _write(root / "src" / "a.py", "def kept_symbol() -> None:\n    return None\n")
    outside = tmp_path / "outside"
    _write(outside / "a.py", "def outside_secret() -> None:\n    return None\n" + "# pad\n" * 50)
    target = root / "src" / "a.py"

    def mutate() -> None:
        if swap == "directory-symlink":
            (root / "src" / "a.py").unlink()
            (root / "src").rmdir()
            (root / "src").symlink_to(outside, target_is_directory=True)
        elif swap == "file-symlink":
            target.unlink()
            target.symlink_to(outside / "a.py")
        elif swap == "in-root-symlink":  # to a file the walk excluded: resolving would index it
            _write(root / ".trw" / "secret.py", "def outside_secret() -> None:\n    return None\n")
            target.unlink()
            target.symlink_to(root / ".trw" / "secret.py")
        elif swap == "fifo":
            target.unlink()
            os.mkfifo(target)
        else:
            target.write_text(target.read_text() + "# grown\n" * 50)

    _after_discovery(monkeypatch, mutate)
    result = update_code_index(root, max_file_bytes=200)

    assert result.stats.skipped == 1 and not result.manifest.files
    assert lexical_search(root, query="outside_secret").results == ()


@pytest.mark.timeout(30)
def test_the_binary_probe_neither_blocks_on_a_fifo_nor_follows_a_symlink(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe.py"
    os.mkfifo(fifo)
    _write(tmp_path / "outside.py")
    link = tmp_path / "link.py"
    link.symlink_to(tmp_path / "outside.py")

    (tmp_path / "linked-dir").symlink_to(tmp_path, target_is_directory=True)

    assert discovery_module._skip_as_binary(tmp_path, fifo) is True
    assert discovery_module._skip_as_binary(tmp_path, link) is True
    assert discovery_module._skip_as_binary(tmp_path, tmp_path / "linked-dir" / "outside.py") is True  # a parent
    assert discovery_module._skip_as_binary(tmp_path, tmp_path / "outside.py") is False


@pytest.mark.parametrize("level", [".trw", ".trw/code-index"])
def test_the_build_refuses_to_write_through_a_symlinked_index_directory(tmp_path: Path, level: str) -> None:
    root = tmp_path / "repo"
    _write(root / "a.py")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    if level == ".trw/code-index":
        (root / ".trw").mkdir()
    (root / level).symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(ValueError, match="not a real directory"):
        update_code_index(root)

    assert sorted(p.name for p in elsewhere.rglob("*")) == []


@pytest.mark.timeout(30)
def test_the_previous_manifest_is_never_read_through_a_symlink_or_a_fifo(tmp_path: Path) -> None:
    from trw_mcp.code_index.storage import default_manifest_path, load_manifest

    _write(tmp_path / "a.py")
    update_code_index(tmp_path)
    manifest = default_manifest_path(tmp_path)
    real = tmp_path / "real-manifest.json"
    manifest.rename(real)
    manifest.symlink_to(real)
    assert load_manifest(real) is not None
    assert load_manifest(manifest) is None

    manifest.unlink()
    os.mkfifo(manifest)
    assert load_manifest(manifest) is None


def test_a_manifest_the_next_build_could_not_read_back_is_refused_before_anything_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "a.py")
    update_code_index(tmp_path)
    before = _store_fingerprint(tmp_path)
    _write(tmp_path / "b.py")
    monkeypatch.setattr("trw_mcp.code_index.update.MAX_MANIFEST_BYTES", 100)

    with pytest.raises(ValueError, match="lower build_max_files"):
        update_code_index(tmp_path)

    assert _store_fingerprint(tmp_path) == before


@pytest.mark.parametrize("damage", ["oversized", "corrupt"])
def test_a_scoped_update_over_an_unreadable_manifest_is_refused_not_narrowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    """Out-of-scope rows come from the manifest, so without it a scoped build would drop them."""
    from trw_mcp.code_index.storage import default_manifest_path

    _write(tmp_path / "a" / "one.py")
    _write(tmp_path / "b" / "two.py")
    update_code_index(tmp_path)
    before = _store_fingerprint(tmp_path)
    if damage == "oversized":
        monkeypatch.setattr("trw_mcp.code_index.storage.MAX_MANIFEST_BYTES", 10)
    else:
        default_manifest_path(tmp_path).write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="without --paths"):
        update_code_index(tmp_path, paths=["a"])

    assert _store_fingerprint(tmp_path) == before
    assert update_code_index(tmp_path, paths=["."]).stats.total_files == 2  # a whole-tree scope is a full build


def test_a_failed_manifest_write_leaves_no_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.code_index.storage import default_manifest_path, load_manifest, save_manifest

    _write(tmp_path / "a.py")
    manifest = update_code_index(tmp_path).manifest
    monkeypatch.setattr("trw_mcp.code_index.storage.manifest_text", lambda _m: 1 / 0)

    with pytest.raises(ZeroDivisionError):
        save_manifest(default_manifest_path(tmp_path), manifest)

    assert [p.name for p in default_manifest_path(tmp_path).parent.iterdir() if p.name.endswith(".tmp")] == []
    assert load_manifest(default_manifest_path(tmp_path)) == manifest


def test_the_build_chunks_only_the_bytes_the_manifest_hashed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file rewritten between the hash and the chunking is a failed file, not a mismatched row."""
    _write(tmp_path / "a.py", "def hashed_symbol() -> None:\n    return None\n")
    from trw_mcp.code_index import store as store_module

    real = store_module.read_indexed_file

    def rewritten(root: Path, relative: str, cap: int) -> bytes:
        (root / relative).write_text("def swapped_symbol() -> None:\n    return None\n")
        return real(root, relative, cap)

    monkeypatch.setattr(store_module, "read_indexed_file", rewritten)
    result = update_code_index(tmp_path)

    assert result.chunk_stats.failed_files == 1
    assert lexical_search(tmp_path, query="swapped_symbol").results == ()


# --- lifecycle ---------------------------------------------------------------


def test_a_query_without_a_store_returns_index_missing_and_writes_nothing(tmp_path: Path) -> None:
    _write(tmp_path / "a.py")

    response = symbol_search(tmp_path, symbol="f")

    assert response.error_code == "index_missing"
    assert "trw-mcp code index" in response.remediation
    assert not (tmp_path / ".trw").exists()


def test_a_legacy_chunks_json_is_never_opened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = tmp_path / ".trw" / "code-index" / "chunks.json"
    _write(legacy, "{}")
    real_open = open

    def guarded_open(file: object, *args: object, **kwargs: object) -> object:
        if str(file).endswith("chunks.json"):
            raise AssertionError("the legacy chunks.json was opened")
        return real_open(file, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr("builtins.open", guarded_open)
    monkeypatch.setattr(Path, "read_text", lambda *_a, **_k: pytest.fail("read_text"))
    monkeypatch.setattr(Path, "read_bytes", lambda *_a, **_k: pytest.fail("read_bytes"))

    assert lexical_search(tmp_path, query="anything").error_code == "index_missing"


def test_a_corrupt_store_returns_index_corrupt(tmp_path: Path) -> None:
    _write(tmp_path / "a.py")
    update_code_index(tmp_path)
    default_store_path(tmp_path).write_bytes(b"not a database" * 100)

    assert symbol_search(tmp_path, symbol="f").error_code == "index_corrupt"


_ENDLESS = "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT {cols} FROM r"


def _craft(root: Path, table: str, cols: str) -> None:
    """Swap one of the published store's tables for a view that never ends (rc5 C12)."""
    with sqlite3.connect(default_store_path(root)) as conn:
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"CREATE VIEW {table} AS {_ENDLESS.format(cols=cols)}")


@pytest.mark.timeout(60)
def test_a_store_whose_meta_is_an_endless_view_is_refused_unread(tmp_path: Path) -> None:
    _write(tmp_path / "a.py")
    update_code_index(tmp_path)
    _craft(tmp_path, "meta", "'k' AS key, n AS value")

    assert lexical_search(tmp_path, query="f").error_code == "index_corrupt"


@pytest.mark.timeout(60)
@pytest.mark.parametrize(
    ("table", "cols"), [("meta", "'k' AS key, n AS value"), ("chunks", "n AS path, n AS file_sha256")]
)
def test_a_build_over_a_crafted_previous_store_refuses_it_unread(tmp_path: Path, table: str, cols: str) -> None:
    """A crafted previous store is never read: every build chunks from source and publishes a fresh store."""
    _write(tmp_path / "a.py")
    update_code_index(tmp_path)
    _craft(tmp_path, table, cols)

    result = update_code_index(tmp_path, force=True, bounds=CodeIndexBounds(build_timeout_seconds=3))

    assert result.chunk_stats.indexed_files == 1
    assert lexical_search(tmp_path, query="f").status == "ok"


@pytest.mark.timeout(60)
def test_a_build_never_copies_a_crafted_previous_chunk(tmp_path: Path) -> None:
    """A view whose path and hash match the file would be copied row for row; the schema check refuses it first."""
    _write(tmp_path / "a.py")
    update_code_index(tmp_path)
    columns = dict.fromkeys(CHUNK_COLUMNS, "''") | {
        "path": "'a.py'",
        "file_sha256": f"'{hashlib.sha256((tmp_path / 'a.py').read_bytes()).hexdigest()}'",
        "start_line": "1",
        "end_line": "1",
        "ast_available": "0",
        "text": f"({_ENDLESS.format(cols='max(n)')})",
    }
    with sqlite3.connect(default_store_path(tmp_path)) as conn:
        conn.execute("DROP TABLE chunks")
        conn.execute(f"CREATE VIEW chunks AS SELECT {', '.join(f'{v} AS {c}' for c, v in columns.items())}")

    result = update_code_index(tmp_path, bounds=CodeIndexBounds(build_timeout_seconds=3))

    assert result.chunk_stats.indexed_files == 1
    assert symbol_search(tmp_path, symbol="f").results[0].path == "a.py"


# rc7 C12: one value computed inside a single VM step never reaches the progress handler.
_HUGE = "hex(zeroblob(32 * 1024 * 1024))"
_ROW = dict.fromkeys(CHUNK_COLUMNS, "'x'") | {
    "chunk_id": "'c' || hex(zeroblob(8))",
    "path": "'a.py'",
    "file_sha256": f"'{'0' * 64}'",
    "text_hash": f"'{'0' * 64}'",
    "symbol_name": "'f'",
    "symbol_kind": "'function'",
    "start_line": "1",
    "end_line": "1",
    "ast_available": "0",
}
_CHUNKS_DDL = ", ".join(
    f"{column} {'INTEGER' if column.endswith(('_line', 'available')) else 'TEXT'}" for column in CHUNK_COLUMNS
)


@pytest.mark.timeout(60)
@pytest.mark.parametrize(
    "statements",
    [
        pytest.param(
            [
                "DROP TABLE chunks",
                f"CREATE VIEW chunks AS SELECT {', '.join(f'{v} AS {c}' for c, v in (_ROW | {'text': _HUGE}).items())}",
            ],
            id="view",
        ),
        pytest.param(
            [
                "DROP TABLE chunks",
                f"CREATE TABLE chunks ({_CHUNKS_DDL.replace('text TEXT', f'text TEXT GENERATED ALWAYS AS ({_HUGE}) VIRTUAL')})",
                f"INSERT INTO chunks ({', '.join(c for c in CHUNK_COLUMNS if c != 'text')}) "
                f"SELECT {', '.join(v for c, v in _ROW.items() if c != 'text')}",
            ],
            id="generated-column",
        ),
        pytest.param(["CREATE TRIGGER t AFTER DELETE ON meta BEGIN SELECT 1; END"], id="trigger"),
        pytest.param(["CREATE INDEX chunks_by_text ON chunks (upper(text))"], id="expression-index"),
    ],
)
def test_a_store_whose_schema_is_not_the_builds_is_refused_unread(tmp_path: Path, statements: list[str]) -> None:
    _write(tmp_path / "a.py")
    update_code_index(tmp_path)
    with sqlite3.connect(default_store_path(tmp_path)) as conn:
        for statement in statements:
            conn.execute(statement)

    response = symbol_search(tmp_path, symbol="f")

    assert (response.status, response.error_code) == ("failed", "index_corrupt")
    assert "schema" in response.error


@pytest.mark.skipif(sys.version_info < (3, 11), reason="Connection.setlimit is 3.11+")
def test_a_stored_value_over_the_length_limit_is_refused_not_loaded(tmp_path: Path) -> None:
    _write(tmp_path / "a.py")
    update_code_index(tmp_path)
    with sqlite3.connect(default_store_path(tmp_path)) as conn:
        conn.execute(f"UPDATE chunks SET text = {_HUGE}")

    response = symbol_search(tmp_path, symbol="f")

    assert (response.status, response.error_code) == ("failed", "index_corrupt")
    assert "too big" in response.error


def test_a_malformed_row_is_a_corrupt_store_not_a_crashed_query(tmp_path: Path) -> None:
    _write(tmp_path / "a.py")
    update_code_index(tmp_path)
    with sqlite3.connect(default_store_path(tmp_path)) as conn:
        conn.execute("UPDATE chunks SET symbol_kind = 'not-a-kind'")

    assert symbol_search(tmp_path, symbol="f").error_code == "index_corrupt"

    # rc11 F2: the rebuild the error advertises recovers the store; nothing is read back from the old one.
    update_code_index(tmp_path)

    assert symbol_search(tmp_path, symbol="f").status == "ok"
    with sqlite3.connect(default_store_path(tmp_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chunks WHERE symbol_kind = 'not-a-kind'").fetchone()[0] == 0


def test_a_row_that_cannot_be_decoded_is_repaired_by_the_rebuild(tmp_path: Path) -> None:
    """rc11 F2b: a TEXT value that is not UTF-8 fails in the driver, before any validator; the rebuild never reads it."""
    _write(tmp_path / "a.py")
    update_code_index(tmp_path)
    with sqlite3.connect(default_store_path(tmp_path)) as conn:
        conn.execute("UPDATE chunks SET text = CAST(X'80' AS TEXT)")

    update_code_index(tmp_path)

    assert symbol_search(tmp_path, symbol="f").status == "ok"


@pytest.mark.timeout(60)
def test_an_unchanged_file_with_many_large_prior_rows_is_not_loaded(tmp_path: Path) -> None:
    """rc11 F2b: rows carrying the file's own hash are the checkout's to multiply; the rebuild chunks the source."""
    _write(tmp_path / "a.py")
    update_code_index(tmp_path)
    with sqlite3.connect(default_store_path(tmp_path)) as conn:
        row = conn.execute(f"SELECT {', '.join(CHUNK_COLUMNS)} FROM chunks").fetchone()
        text_at = CHUNK_COLUMNS.index("text")
        id_at = CHUNK_COLUMNS.index("chunk_id")
        conn.executemany(
            f"INSERT INTO chunks ({', '.join(CHUNK_COLUMNS)}) VALUES ({', '.join('?' for _ in CHUNK_COLUMNS)})",
            [
                (
                    *row[:id_at],
                    f"crafted-chunk-{i:08d}",
                    *row[id_at + 1 : text_at],
                    "x" * 1_000_000,
                    *row[text_at + 1 :],
                )
                for i in range(200)
            ],
        )

    result = update_code_index(tmp_path, bounds=CodeIndexBounds(build_timeout_seconds=5))

    assert result.chunk_stats.total_chunks == 1  # the crafted rows were never loaded or copied
    assert result.chunk_stats.indexed_files == 1


def test_crafted_rows_that_never_match_cannot_outrun_the_deadline(tmp_path: Path) -> None:
    """Rows the LIKE filter rejects never reach Python, so only the progress handler sees them (rc7 sol review)."""
    _write(tmp_path / "a.py")
    update_code_index(tmp_path)
    with sqlite3.connect(default_store_path(tmp_path)) as conn:
        row = conn.execute(f"SELECT {', '.join(CHUNK_COLUMNS)} FROM chunks").fetchone()
        conn.executemany(
            f"INSERT INTO chunks ({', '.join(CHUNK_COLUMNS)}) VALUES ({', '.join('?' for _ in CHUNK_COLUMNS)})",
            [(*row[:-1], "a" * 3_900_000)] * 10,
        )
    # Terms whose first letter never occurs keep each row's LIKEs cheap (~65 ms), so this pins the handler's interval;
    # one row's own scan is bounded by the length limit, not by the deadline.
    query = " ".join(f"zz{index}" for index in range(64))

    started = time.monotonic()
    response = lexical_search(tmp_path, query=query, bounds=CodeIndexBounds(query_timeout_seconds=0.05))

    assert response.bound == "code_index_bounds.query_timeout_seconds"
    assert time.monotonic() - started < 0.4  # ten such rows ran blind for ~0.7 s at the old 10,000-step interval


@pytest.mark.parametrize("value", ["CAST('evil' AS BLOB)", "hex(zeroblob(1024))"])
def test_store_metadata_that_is_not_a_short_string_is_a_corrupt_store(tmp_path: Path, value: str) -> None:
    """A TEXT column still stores a BLOB; the revision reaches a strict response field, so it is checked first."""
    _write(tmp_path / "a.py")
    update_code_index(tmp_path)
    with sqlite3.connect(default_store_path(tmp_path)) as conn:
        conn.execute(f"UPDATE meta SET value = {value} WHERE key = 'revision'")

    assert symbol_search(tmp_path, symbol="f").error_code == "index_corrupt"


def test_the_query_deadline_binds_however_few_rows_it_scans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Before rc7 C12 a query under 1,000 rows never checked its deadline, so a slow row answered ok."""
    _write(tmp_path / "a.py")
    update_code_index(tmp_path)
    from trw_mcp.code_index.store import row_to_chunk

    def slow(row: sqlite3.Row) -> object:
        time.sleep(0.6)
        return row_to_chunk(row)

    monkeypatch.setattr("trw_mcp.code_index.search.row_to_chunk", slow)
    response = symbol_search(tmp_path, symbol="f", bounds=CodeIndexBounds(query_timeout_seconds=0.5))

    assert (response.error_code, response.bound) == ("index_bound_exceeded", "code_index_bounds.query_timeout_seconds")


def test_below_python_3_11_the_code_index_neither_reads_nor_builds_a_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only 3.11+ can cap a store value (Connection.setlimit), so 3.10 fails closed by name (rc7 C12, option A)."""
    from types import SimpleNamespace

    from trw_mcp.tools.code_index import build_code_index

    _write(tmp_path / "a.py")
    update_code_index(tmp_path)
    monkeypatch.setattr("trw_mcp.code_index.store.sys", SimpleNamespace(version_info=(3, 10, 14)))
    monkeypatch.setattr("trw_mcp.code_index.update.discover_indexable_files", lambda *_a, **_k: pytest.fail("walked"))
    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_k: pytest.fail("connected"))
    manifest = tmp_path / ".trw" / "code-index" / "manifest.json"
    before = (_store_fingerprint(tmp_path), manifest.read_bytes())

    queried = symbol_search(tmp_path, symbol="f")
    built = build_code_index(str(tmp_path), force=True)

    assert (queried.status, queried.error_code) == ("failed", "unsupported_runtime")
    assert "Python 3.11+" in queried.error and "Python 3.10" in queried.error
    assert (built["status"], built["error_code"]) == ("failed", "unsupported_runtime")
    assert (_store_fingerprint(tmp_path), manifest.read_bytes()) == before


def test_a_file_over_the_indexed_size_cap_is_skipped_whatever_the_config_allows(tmp_path: Path) -> None:
    """The store's length limit assumes no chunk outgrows one file, so the walk never admits a larger one."""
    _write(tmp_path / "small.py")
    _write(tmp_path / "big.py", "x = 1\n" * (MAX_INDEXED_FILE_BYTES // 6 + 1))

    assert _indexed(tmp_path, max_file_bytes=4 * MAX_INDEXED_FILE_BYTES) == {"small.py"}


def test_an_interrupted_store_open_reports_the_query_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path / "a.py")
    update_code_index(tmp_path)

    def interrupted(conn: sqlite3.Connection, schema: str) -> dict[str, str]:
        raise sqlite3.OperationalError("interrupted")

    monkeypatch.setattr("trw_mcp.code_index.store._read_meta", interrupted)
    bounds = CodeIndexBounds(query_timeout_seconds=1e-9)

    assert lexical_search(tmp_path, query="f", bounds=bounds).error_code == "index_bound_exceeded"


def test_a_store_behind_head_answers_and_reports_stale_with_its_revision(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "one")
    _write(tmp_path / "a.py", "def fresh_symbol() -> None:\n    return None\n")
    update_code_index(tmp_path)

    fresh = symbol_search(tmp_path, symbol="fresh_symbol")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "two")
    stale = symbol_search(tmp_path, symbol="fresh_symbol")

    assert (fresh.status, fresh.index_state) == ("ok", "")
    assert (stale.status, stale.index_state) == ("ok", "stale")
    assert stale.index_revision and stale.index_revision == fresh.index_revision
    assert stale.results[0].path == "a.py"


def test_a_query_never_writes_the_store(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "def target() -> None:\n    return None\n")
    update_code_index(tmp_path)
    before = _store_fingerprint(tmp_path)
    _write(tmp_path / "b.py", "def target_two() -> None:\n    return None\n")

    lexical_search(tmp_path, query="target")
    symbol_search(tmp_path, symbol="target")

    assert _store_fingerprint(tmp_path) == before
    assert sorted(p.name for p in default_store_path(tmp_path).parent.iterdir()) == [
        "chunks.sqlite",
        "manifest.json",
    ]


# --- peak memory on a store ten times the row budget -------------------------

_CHILD = textwrap.dedent(
    """
    import builtins, json, pathlib, resource, sys
    from trw_mcp.code_index.bounds import CodeIndexBounds
    from trw_mcp.code_index.search import lexical_search, symbol_search

    root = pathlib.Path(sys.argv[1])
    bounds = CodeIndexBounds(query_max_rows=int(sys.argv[2]))
    store = str(root / ".trw" / "code-index" / "chunks.sqlite")
    real_open = builtins.open

    def guarded(file, *args, **kwargs):
        if str(file) == store:
            raise AssertionError("whole-file read of the store")
        return real_open(file, *args, **kwargs)

    builtins.open = guarded
    pathlib.Path.read_bytes = lambda self: (_ for _ in ()).throw(AssertionError("read_bytes"))
    found = symbol_search(root, symbol="rare_symbol", bounds=bounds)
    searched = lexical_search(root, query="rare_symbol", bounds=bounds)
    flooded = lexical_search(root, query="common", bounds=bounds)
    if sys.platform == "linux":
        status = pathlib.Path("/proc/self/status").read_text()
        peak = int(next(l for l in status.splitlines() if l.startswith("VmHWM:")).split()[1]) * 1024
    else:
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(json.dumps({
        "found": [h.path for h in found.results],
        "searched": [h.path for h in searched.results],
        "flooded": [flooded.error_code, flooded.bound],
        "peak": peak,
    }))
    """
)

_PEAK_CEILING_BYTES = 512 * 1024 * 1024


def _seed_store(root: Path, rows: int) -> None:
    """Publish a real store, then pad it to ``rows`` synthetic chunks sharing the term 'common'."""
    _write(root / "real.py", "def rare_symbol() -> None:\n    return None\n")
    update_code_index(root)
    placeholders = ", ".join("?" for _ in CHUNK_COLUMNS)
    values = {
        "path": "pad.py",
        "file_sha256": "0" * 64,
        "language": "python",
        "symbol_kind": "function",
        "text_hash": "0" * 64,
        "signature": "def pad()",
        "docstring_summary": "",
        "ast_available": 1,
        "text": "def pad():\n    return 'common filler text for the padding rows'\n",
    }
    with sqlite3.connect(default_store_path(root)) as conn:
        conn.executemany(
            f"INSERT INTO chunks ({', '.join(CHUNK_COLUMNS)}) VALUES ({placeholders})",
            (
                tuple(
                    {
                        **values,
                        "chunk_id": f"pad-{i:012d}",
                        "symbol_name": f"pad_{i}",
                        "start_line": i + 1,
                        "end_line": i + 2,
                    }[column]
                    for column in CHUNK_COLUMNS
                )
                for i in range(rows)
            ),
        )


def _run_child(root: Path, query_max_rows: int) -> dict[str, object]:
    src = Path(__file__).resolve().parents[1] / "src"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(src), os.environ.get("PYTHONPATH", "")])}
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, str(root), str(query_max_rows)],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _assert_flat_peak(root: Path, query_max_rows: int) -> None:
    _seed_store(root, rows=10 * query_max_rows)
    before = _store_fingerprint(root)

    report = _run_child(root, query_max_rows)

    assert report["found"] == ["real.py"]
    assert report["searched"] == ["real.py"]
    assert report["flooded"] == ["index_bound_exceeded", "code_index_bounds.query_max_rows"]
    assert int(report["peak"]) < _PEAK_CEILING_BYTES, report["peak"]
    assert _store_fingerprint(root) == before


def test_a_fresh_query_process_stays_flat_on_a_store_ten_times_its_row_budget(tmp_path: Path) -> None:
    """A small row budget exercises the same path as the default one without the default's disk and time."""
    _assert_flat_peak(tmp_path, query_max_rows=2_000)


@pytest.mark.slow
def test_the_default_row_budget_stays_flat_on_a_store_ten_times_its_size(tmp_path: Path) -> None:
    """2M rows at the default budget. Heavy: run alone, never beside other lanes."""
    _assert_flat_peak(tmp_path, query_max_rows=CodeIndexBounds().query_max_rows)


# --- chunking ----------------------------------------------------------------


def test_a_module_constant_and_a_long_function_are_both_findable(tmp_path: Path) -> None:
    """REVIEWER_TOOLS-style constants were never chunked; long definitions were cut at 80 lines."""
    body = "\n".join(f"    step_{i} = {i}" for i in range(200))
    _write(
        tmp_path / "surface.py",
        'REVIEWER_TOOLS: frozenset[str] = frozenset({"trw_recall"})\n'
        "LIMIT = 3\n\n"
        f"def long_function() -> None:\n{body}\n    tail_marker_line = 1\n",
    )
    update_code_index(tmp_path)

    constant = symbol_search(tmp_path, symbol="REVIEWER_TOOLS").results
    assert [(hit.symbol_name, hit.symbol_kind) for hit in constant] == [("REVIEWER_TOOLS", "constant")]
    assert symbol_search(tmp_path, symbol="LIMIT").results[0].line_range.start == 2

    pieces = symbol_search(tmp_path, symbol="long_function").results
    spans = sorted((hit.line_range.start, hit.line_range.end) for hit in pieces)
    assert len(spans) == 3
    assert spans[0][0] == 4 and spans[-1][1] == 205
    assert all(end - start < 80 for start, end in spans)
    assert all(later[0] == earlier[1] + 1 for earlier, later in itertools.pairwise(spans))
    assert lexical_search(tmp_path, query="tail_marker_line").results[0].symbol_name == "long_function"


def test_the_deadline_is_checked_while_processing_the_enumerated_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("a.py", "b.py", "c.py"):
        _write(tmp_path / name)
    counting = _CountingScandir()
    monkeypatch.setattr(discovery_module.os, "scandir", counting)
    # Checks 1-4: the directory boundary and the three entries; check 5 is the first processed entry.
    deadline = _TripAfter(5)

    with pytest.raises(IndexBoundExceeded) as caught:
        discover_indexable_files(tmp_path, deadline=deadline)

    assert caught.value.bound == "code_index_bounds.build_timeout_seconds"
    assert counting.consumed == 3, "enumeration finished; the trip came from the processing loop"


def test_explicit_path_limits_are_charged_to_the_entry_cap_and_the_deadline(tmp_path: Path) -> None:
    for i in range(5):
        _write(tmp_path / f"f{i}.py")
    limits = [f"f{i}.py" for i in range(5)]

    with pytest.raises(IndexBoundExceeded) as capped:
        discover_indexable_files(tmp_path, paths=limits, bounds=CodeIndexBounds(build_max_entries=2))
    assert capped.value.bound == "code_index_bounds.build_max_entries"

    deadline = _TripAfter(2)
    with pytest.raises(IndexBoundExceeded) as timed:
        discover_indexable_files(tmp_path, paths=limits, deadline=deadline)
    assert timed.value.bound == "code_index_bounds.build_timeout_seconds"
    assert deadline.calls == 2, "the second limit tripped it; the rest were never visited"


def test_a_listing_that_fails_midway_is_skipped_as_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path / "ok.py")
    for name in ("x.py", "y.py", "z.py"):
        _write(tmp_path / "flaky" / name)
    real_scandir = os.scandir
    flaky = (tmp_path / "flaky").resolve()  # discovery walks the resolved root

    class _FailsAfterOne:
        def __init__(self, path: str | os.PathLike[str]) -> None:
            self._inner = real_scandir(path)
            # Only this test's directory, by path: a descriptor or any other walker passes through.
            self._fail = isinstance(path, (str, os.PathLike)) and Path(path).resolve() == flaky
            self._yielded = 0

        def __enter__(self) -> _FailsAfterOne:
            self._it = self._inner.__enter__()
            return self

        def __exit__(self, *exc: object) -> None:
            self._inner.__exit__(*exc)

        def __iter__(self) -> _FailsAfterOne:
            return self

        def __next__(self) -> os.DirEntry[str]:
            if self._fail and self._yielded == 1:
                raise OSError("device went away")
            self._yielded += 1
            return next(self._it)

    # discovery_module.os is the global os module: undo the fake as soon as the call returns, so no
    # teardown walker (tmp_path cleanup, a conftest sweep) lists "flaky" through it (rc9 Linux E).
    with monkeypatch.context() as patch:
        patch.setattr(discovery_module.os, "scandir", _FailsAfterOne)
        result = discover_indexable_files(tmp_path)

        assert [p.name for p in result.files] == ["ok.py"]
        assert result.unreadable_dirs == 1

        # The root's two entries plus the one consumed from "flaky" make three: the
        # consumed entry stays charged, so a cap of two trips even though "flaky" failed.
        discover_indexable_files(tmp_path, bounds=CodeIndexBounds(build_max_entries=3))
        with pytest.raises(IndexBoundExceeded) as caught:
            discover_indexable_files(tmp_path, bounds=CodeIndexBounds(build_max_entries=2))
        assert caught.value.bound == "code_index_bounds.build_max_entries"
