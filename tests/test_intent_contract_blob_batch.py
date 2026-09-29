"""One ``git cat-file --batch`` per history walk must answer exactly like ``read_blob``."""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import threading
from pathlib import Path

import pytest

from tests._intent_contract_git import commit_all, git, init_repo, pytest_skip_no_git
from trw_mcp.security.intent_contract import _git_run
from trw_mcp.security.intent_contract._git_run import (
    BlobUnreadable,
    GitBlobBatch,
    _read_exact,
    blob_batch,
    read_blob,
)

pytestmark = pytest_skip_no_git

HEADER_LOOKALIKE = b"0123456789012345678901234567890123456789 blob 5\nhello\n" + b"\n" + b"0" * 40 + b" missing\n"
BINARY = b"\x00\x01\xffabc\x00\n\r\n\x00"
LARGE = (b"line of text\n" * 8000) + b"\x00tail"  # > 64 KiB, includes a NUL


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = init_repo(tmp_path)
    (root / "plain.txt").write_bytes(b"hello\n")
    (root / "binary.bin").write_bytes(BINARY)
    (root / "lookalike.txt").write_bytes(HEADER_LOOKALIKE)
    (root / "large.bin").write_bytes(LARGE)
    (root / "empty.txt").write_bytes(b"")
    (root / "dir").mkdir()
    (root / "dir" / "inner.txt").write_bytes(b"x")
    (root / "name with space.txt").write_bytes(b"spaced")
    commit_all(root, "seed")
    return root


def _no_per_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if a read falls back to the two-spawn path."""

    def _boom(*_args: object, **_kwargs: object) -> bytes | None:
        raise AssertionError("read fell back to per-spawn git")

    monkeypatch.setattr(_git_run, "_read_blob_per_spawn", _boom)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("plain.txt", b"hello\n"),
        ("binary.bin", BINARY),
        ("lookalike.txt", HEADER_LOOKALIKE),
        ("large.bin", LARGE),
        ("empty.txt", b""),
        ("name with space.txt", b"spaced"),
        ("nope/missing.txt", None),
    ],
)
def test_batch_answers_match_read_blob(
    repo: Path, monkeypatch: pytest.MonkeyPatch, path: str, expected: bytes | None
) -> None:
    per_spawn = read_blob(repo, "HEAD", path)
    assert per_spawn == expected
    _no_per_spawn(monkeypatch)
    with blob_batch(repo):
        assert read_blob(repo, "HEAD", path) == expected


def test_large_blob_is_over_one_pipe_buffer() -> None:
    assert len(LARGE) > 64 * 1024


def test_many_reads_share_one_process(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_per_spawn(monkeypatch)
    with blob_batch(repo) as batch:
        first_pid = batch._proc.pid if batch._proc else -1
        for _ in range(3):
            assert read_blob(repo, "HEAD", "plain.txt") == b"hello\n"
            assert read_blob(repo, "HEAD", "large.bin") == LARGE
        assert batch._proc is not None
        assert batch._proc.pid == first_pid
        assert batch._proc.poll() is None


def test_unresolvable_rev_still_raises_unreadable(repo: Path) -> None:
    with pytest.raises(BlobUnreadable):
        read_blob(repo, "deadbeef" * 5, "plain.txt")
    with blob_batch(repo), pytest.raises(BlobUnreadable):
        read_blob(repo, "deadbeef" * 5, "plain.txt")


def test_directory_is_not_a_blob(repo: Path) -> None:
    with pytest.raises(BlobUnreadable):
        read_blob(repo, "HEAD", "dir")
    with blob_batch(repo), pytest.raises(BlobUnreadable):
        read_blob(repo, "HEAD", "dir")


def test_none_rev_is_empty_tree(repo: Path) -> None:
    with blob_batch(repo):
        assert read_blob(repo, None, "plain.txt") is None


def test_other_repo_is_not_served_by_the_batch(repo: Path, tmp_path: Path) -> None:
    other = init_repo(tmp_path, "other")
    (other / "plain.txt").write_bytes(b"other\n")
    commit_all(other, "other")
    with blob_batch(repo):
        assert read_blob(other, "HEAD", "plain.txt") == b"other\n"
        assert read_blob(repo, "HEAD", "plain.txt") == b"hello\n"


def test_process_is_closed_when_the_body_raises(repo: Path) -> None:
    captured: list[GitBlobBatch] = []
    with pytest.raises(RuntimeError, match="boom"), blob_batch(repo) as batch:
        captured.append(batch)
        assert read_blob(repo, "HEAD", "plain.txt") == b"hello\n"
        proc = batch._proc
        assert proc is not None
        raise RuntimeError("boom")
    assert proc.poll() is not None
    assert _git_run._ACTIVE_BATCH.get() is None
    # After close, reads still answer (per-spawn) rather than returning stale/None.
    assert read_blob(repo, "HEAD", "plain.txt") == b"hello\n"


def test_dead_batch_process_fails_over_to_the_per_spawn_answer(repo: Path) -> None:
    with blob_batch(repo) as batch:
        assert read_blob(repo, "HEAD", "plain.txt") == b"hello\n"
        assert batch._proc is not None
        batch._proc.kill()
        batch._proc.wait()
        assert read_blob(repo, "HEAD", "plain.txt") == b"hello\n"
        assert read_blob(repo, "HEAD", "nope.txt") is None
        with pytest.raises(BlobUnreadable):
            read_blob(repo, "deadbeef" * 5, "plain.txt")


def test_dead_batch_is_never_read_as_no_weakening(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A batch that cannot answer AND a git that cannot answer must raise, not return None."""
    with blob_batch(repo) as batch:
        assert batch._proc is not None
        batch._proc.kill()
        batch._proc.wait()

        def _fail(*_args: object, **_kwargs: object) -> str:
            raise _git_run.GitCommandError(("rev-parse",), 128, "broken")

        monkeypatch.setattr(_git_run, "run_git", _fail)
        with pytest.raises(BlobUnreadable):
            read_blob(repo, "HEAD", "plain.txt")


def test_newline_in_path_bypasses_batch_and_still_answers(repo: Path) -> None:
    with blob_batch(repo):
        assert read_blob(repo, "HEAD", "a\nb.txt") is None


def test_batch_matches_git_cat_file_for_every_tracked_path(repo: Path) -> None:
    listed = git(repo, "ls-files").splitlines()
    with blob_batch(repo):
        for name in listed:
            expected = subprocess.run(
                ["git", "cat-file", "blob", f"HEAD:{name}"], cwd=str(repo), capture_output=True, check=True
            ).stdout
            assert read_blob(repo, "HEAD", name) == expected


# --- present-but-unreadable entries must never read as "no contract" -----------------------------


def _outcome(fn: object, *args: object) -> object:
    try:
        return fn(*args)  # type: ignore[operator]
    except BlobUnreadable:
        return "BlobUnreadable"


@pytest.fixture()
def odd_repo(tmp_path: Path) -> Path:
    """A repo with a gitlink to an absent commit and a blob entry whose object was deleted."""
    root = init_repo(tmp_path, "odd")
    (root / "f.txt").write_bytes(b"hello\n")
    (root / "d").mkdir()
    (root / "d" / "kept.txt").write_bytes(b"kept")
    git(root, "add", "-A")
    git(root, "update-index", "--add", "--cacheinfo", "160000,1111111111111111111111111111111111111111,sub")
    git(root, "update-index", "--add", "--cacheinfo", "160000,2222222222222222222222222222222222222222,d/sub")
    doomed: list[str] = []
    for name, data in (("gone.yaml", b"secret contract A\n"), ("d/gone.yaml", b"secret contract B\n")):
        blob = (
            subprocess.run(
                ["git", "hash-object", "-w", "--stdin"], cwd=str(root), input=data, capture_output=True, check=True
            )
            .stdout.decode()
            .strip()
        )
        git(root, "update-index", "--add", "--cacheinfo", f"100644,{blob},{name}")
        doomed.append(blob)
    git(root, "commit", "-q", "-m", "seed odd entries")
    for blob in doomed:
        loose = root / ".git" / "objects" / blob[:2] / blob[2:]
        os.chmod(loose, 0o644)
        os.remove(loose)
    return root


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("sub", "BlobUnreadable"),
        ("d/sub", "BlobUnreadable"),
        ("gone.yaml", "BlobUnreadable"),
        ("d/gone.yaml", "BlobUnreadable"),
        ("absent.yaml", None),
        ("d/absent.yaml", None),
        ("nodir/absent.yaml", None),
        ("f.txt/child", None),
        ("./f.txt", b"hello\n"),
        ("d//kept.txt", None),
    ],
)
def test_unreadable_entries_match_per_spawn_and_fail_closed(odd_repo: Path, path: str, expected: object) -> None:
    per_spawn = _outcome(_git_run._read_blob_per_spawn, odd_repo, "HEAD", path)
    assert per_spawn == expected
    with blob_batch(odd_repo):
        assert _outcome(read_blob, odd_repo, "HEAD", path) == per_spawn


def test_missing_reads_do_not_spawn_git(odd_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with blob_batch(odd_repo):
        _no_per_spawn(monkeypatch)

        def _no_run(*_a: object, **_k: object) -> str:
            raise AssertionError("a missing read spawned git")

        monkeypatch.setattr(_git_run, "run_git", _no_run)
        assert read_blob(odd_repo, "HEAD", "d/absent.yaml") is None
        with pytest.raises(BlobUnreadable):
            read_blob(odd_repo, "HEAD", "gone.yaml")


# --- concurrency ---------------------------------------------------------------------------------


def test_concurrent_readers_get_their_own_contents(repo: Path) -> None:
    errors: list[str] = []
    barrier = threading.Barrier(2)

    def work(path: str, expected: bytes) -> None:
        barrier.wait()
        for _ in range(150):
            if batch.read("HEAD", path) != expected:
                errors.append(path)
                return

    with blob_batch(repo) as batch:
        threads = [
            threading.Thread(target=work, args=("large.bin", LARGE)),
            threading.Thread(target=work, args=("plain.txt", b"hello\n")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert errors == []


# --- guard for specs the line protocol cannot carry ----------------------------------------------


@pytest.mark.parametrize(("rev", "path"), [("HEAD", "a\rb"), ("HEAD", "a\0b"), ("-HEAD", "plain.txt"), ("HE\rAD", "x")])
def test_unsafe_specs_never_reach_the_batch(repo: Path, rev: str, path: str) -> None:
    with blob_batch(repo) as batch:
        calls: list[str] = []
        original = batch.read
        batch.read = lambda r, p: (calls.append(p), original(r, p))[1]  # type: ignore[method-assign]
        with contextlib.suppress(BlobUnreadable, ValueError):
            read_blob(repo, rev, path)
        assert calls == []


# --- fallback branches ---------------------------------------------------------------------------


class _FakeProc:
    """Stands in for the batch child: scripted stdout, recorded stdin."""

    def __init__(self, stdout: bytes = b"", *, hang: bool = False) -> None:
        self.writes: list[bytes] = []
        self.killed = threading.Event()
        self._hang = hang
        outer = self

        class _In:
            def write(self, data: bytes) -> int:
                outer.writes.append(data)
                return len(data)

            def flush(self) -> None:
                return None

        class _Out(io.BytesIO):
            def readline(self, size: int | None = -1) -> bytes:
                if outer._hang:
                    outer.killed.wait(5)
                    return b""
                return super().readline(size)

        self.stdin = _In()
        self.stdout = _Out(stdout)

    def kill(self) -> None:
        self.killed.set()


def _fake_batch(proc: _FakeProc, timeout: float = 30.0) -> GitBlobBatch:
    batch = GitBlobBatch(Path("."), timeout=timeout)
    batch._proc = proc  # type: ignore[assignment]
    return batch


@pytest.mark.parametrize(
    "stdout",
    [
        b"garbage header line\n",
        b"not-hex blob 3\nabc\n",
        b"0123456789012345678901234567890123456789 blob 3\nabc",  # missing trailing newline
        b"0123456789012345678901234567890123456789 blob 10\nabc\n",  # short read
        b"0123456789012345678901234567890123456789 blob 3\nabcX",  # bad terminator
        b"0123456789012345678901234567890123456789 blob 3",  # header without newline
        b"",
    ],
)
def test_protocol_violation_falls_back_and_latches(stdout: bytes) -> None:
    proc = _FakeProc(stdout)
    batch = _fake_batch(proc)
    assert isinstance(batch.read("HEAD", "x"), _git_run._Fallback)
    assert batch._broken
    writes = len(proc.writes)
    assert isinstance(batch.read("HEAD", "y"), _git_run._Fallback)
    assert len(proc.writes) == writes  # a latched batch never touches the pipe again


def test_watchdog_timeout_falls_back() -> None:
    proc = _FakeProc(hang=True)
    batch = _fake_batch(proc, timeout=0.05)
    assert isinstance(batch.read("HEAD", "x"), _git_run._Fallback)
    assert proc.killed.is_set()
    assert batch._broken


def test_interrupted_query_latches_broken() -> None:
    proc = _FakeProc()

    def _interrupt(_size: int | None = -1) -> bytes:
        raise KeyboardInterrupt

    proc.stdout.readline = _interrupt  # type: ignore[method-assign]
    batch = _fake_batch(proc)
    with pytest.raises(KeyboardInterrupt):
        batch.read("HEAD", "x")
    assert batch._broken


def test_read_exact_short_read_is_none() -> None:
    assert _read_exact(io.BytesIO(b"ab"), 5) is None
    assert _read_exact(io.BytesIO(b"abcde"), 5) == b"abcde"
