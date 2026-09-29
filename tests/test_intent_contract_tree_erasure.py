"""A deleted intermediate tree object must read as unreadable, never as "no contract at this rev"."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests._intent_contract_git import commit_all, git, init_repo, pytest_skip_no_git
from trw_mcp.security.intent_contract import _git_run
from trw_mcp.security.intent_contract._git_run import BlobUnreadable, blob_batch, read_blob

pytestmark = pytest_skip_no_git


def _per_spawn(repo: Path, rev: str, path: str) -> bytes | None:
    return _git_run._read_blob_per_spawn(repo, rev, path)


#: ``_broken`` of the batch that served the LAST ``_batched`` read: True means it fell back to per-spawn.
_LAST_BATCH_BROKEN: list[bool] = []


def _batched(repo: Path, rev: str, path: str) -> bytes | None:
    with blob_batch(repo) as batch:
        try:
            return read_blob(repo, rev, path)
        finally:
            _LAST_BATCH_BROKEN[:] = [batch._broken]


def _outcome_native(reader: object, repo: Path, path: str) -> object:
    """``_outcome``, and for the batch reader also prove the batch ANSWERED (never latched a fallback)."""
    outcome = _outcome(reader, repo, path)
    if reader is _batched:
        assert _LAST_BATCH_BROKEN == [False], f"batch fell back to per-spawn for {path!r}"
    return outcome


READERS = pytest.mark.parametrize("reader", [_per_spawn, _batched], ids=["per_spawn", "batch"])


def _outcome(reader: object, repo: Path, path: str) -> object:
    try:
        value = reader(repo, "HEAD", path)  # type: ignore[operator]
    except BlobUnreadable:
        return "unreadable"
    return "absent" if value is None else value


def _delete_object(repo: Path, spec: str) -> None:
    oid = git(repo, "rev-parse", spec).strip()
    target = repo / ".git" / "objects" / oid[:2] / oid[2:]
    assert target.exists(), "expected a loose object"
    os.chmod(target, 0o644)
    target.unlink()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = init_repo(tmp_path)
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "contract.yaml").write_text("rules: strict\n")
    (root / "a" / "file.txt").write_text("f\n")
    (root / "a" / "sibling.txt").write_text("s\n")
    (root / "top.txt").write_text("x\n")
    commit_all(root, "seed")
    return root


@READERS
def test_intact_path_reads(reader: object, repo: Path) -> None:
    assert _outcome(reader, repo, "a/b/contract.yaml") == b"rules: strict\n"


@READERS
@pytest.mark.parametrize("erased", ["HEAD:a/b", "HEAD:a"])
def test_deleted_ancestor_tree_fails_closed(reader: object, repo: Path, erased: str) -> None:
    _delete_object(repo, erased)
    assert _outcome(reader, repo, "a/b/contract.yaml") == "unreadable"


@READERS
def test_deleted_grandparent_also_blocks_a_deeper_absent_leaf(reader: object, repo: Path) -> None:
    _delete_object(repo, "HEAD:a")
    assert _outcome(reader, repo, "a/b/other.yaml") == "unreadable"


@READERS
def test_deleted_tree_does_not_affect_paths_that_avoid_it(reader: object, repo: Path) -> None:
    _delete_object(repo, "HEAD:a/b")
    assert _outcome(reader, repo, "top.txt") == b"x\n"
    assert _outcome(reader, repo, "a/sibling.txt") == b"s\n"
    assert _outcome(reader, repo, "a/missing.txt") == "absent"
    assert _outcome(reader, repo, "zzz/x.yaml") == "absent"


@READERS
@pytest.mark.parametrize(
    "path",
    ["a/b/absent.yaml", "a/absent/contract.yaml", "absent-dir/x/y.yaml", "absent.yaml", "a/file.txt/x", "top.txt/x/y"],
)
def test_genuinely_absent_paths_stay_none(reader: object, repo: Path, path: str) -> None:
    assert _outcome(reader, repo, path) == "absent"


@pytest.mark.parametrize(
    "setup",
    [None, "HEAD:a/b", "HEAD:a", "HEAD:a/b/contract.yaml"],
    ids=["intact", "no-a/b-tree", "no-a-tree", "no-leaf-blob"],
)
@pytest.mark.parametrize(
    "path",
    ["a/b/contract.yaml", "a/b/absent.yaml", "a/absent/x", "absent/x/y", "a/file.txt/x", "a/sibling.txt", "top.txt"],
)
def test_both_paths_agree(repo: Path, setup: str | None, path: str) -> None:
    if setup is not None:
        _delete_object(repo, setup)
    assert _outcome(_per_spawn, repo, path) == _outcome(_batched, repo, path)


@READERS
def test_unresolvable_rev_still_raises(reader: object, repo: Path) -> None:
    with pytest.raises(BlobUnreadable):
        reader(repo, "deadbeef" * 5, "a/b/contract.yaml")  # type: ignore[operator]


@READERS
def test_deleted_leaf_blob_fails_closed(reader: object, repo: Path) -> None:
    """The tree is intact and lists the leaf, but the blob object is gone: unreadable, never absent."""
    _delete_object(repo, "HEAD:a/b/contract.yaml")
    assert _outcome(reader, repo, "a/b/contract.yaml") == "unreadable"
    assert _outcome(reader, repo, "a/b/other.yaml") == "absent"


def _plumb(repo: Path, *args: str, stdin: bytes = b"") -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}  # never inherit GIT_DIR/GIT_INDEX_FILE
    env |= {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e.test"}
    env |= {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e.test"}
    done = subprocess.run(["git", *args], cwd=str(repo), input=stdin, capture_output=True, check=True, env=env)
    return done.stdout.decode().strip()


def _mktree(repo: Path, entries: list[tuple[str, str, str, str]]) -> str:
    """``entries`` are ``(name, oid, kind, mode)``; the name may carry surrogate-escaped bytes."""
    lines = [
        f"{mode} {kind} {oid}\t".encode() + name.encode("utf-8", "surrogateescape") + b"\0"
        for name, oid, kind, mode in entries
    ]
    return _plumb(repo, "mktree", "-z", stdin=b"".join(lines))


def _hazard_repo(root: Path, ancestor: str) -> tuple[Path, str]:
    """Commit ``<ancestor>/contract.yaml`` plus prefix-sharing siblings, built by plumbing (no filesystem names)."""
    repo = init_repo(root)
    outer: list[tuple[str, str, str, str]] = []
    ancestor_tree = ""
    for name in dict.fromkeys(["a", "a b", "a.d", ancestor]):
        blob = _plumb(
            repo, "hash-object", "-w", "--stdin", stdin=f"content of {name!r}\n".encode("utf-8", "surrogateescape")
        )
        tree = _mktree(repo, [("contract.yaml", blob, "blob", "100644")])
        outer.append((name, tree, "tree", "040000"))
        if name == ancestor:
            ancestor_tree = tree
    commit = _plumb(repo, "commit-tree", _mktree(repo, outer), "-m", "hazard")
    _plumb(repo, "update-ref", "HEAD", commit)
    return repo, ancestor_tree


HAZARD_NAMES = ["a b", "a\tb", "d\udcff\udcfe", "a", "a.d"]


@READERS
@pytest.mark.parametrize("ancestor", HAZARD_NAMES)
def test_parse_hazard_ancestor_names(reader: object, tmp_path: Path, ancestor: str) -> None:
    repo, ancestor_tree = _hazard_repo(tmp_path, ancestor)
    expect = f"content of {ancestor!r}\n".encode("utf-8", "surrogateescape")
    assert _outcome(reader, repo, f"{ancestor}/contract.yaml") == expect
    assert _outcome(reader, repo, f"{ancestor}/absent.yaml") == "absent"
    (repo / ".git" / "objects" / ancestor_tree[:2] / ancestor_tree[2:]).unlink()
    assert _outcome(reader, repo, f"{ancestor}/contract.yaml") == "unreadable"
    assert _outcome(reader, repo, f"{ancestor}/absent.yaml") == "unreadable"
    for sibling in {"a", "a b", "a.d"} - {ancestor}:  # a shared name prefix must not implicate the siblings
        assert isinstance(_outcome(reader, repo, f"{sibling}/contract.yaml"), bytes)


def _non_tree_ancestor_repo(root: Path, kind: str) -> Path:
    repo = init_repo(root)
    (repo / "top.txt").write_text("x\n")
    if kind == "symlink":
        (repo / "a").symlink_to("top.txt")
        commit_all(repo, "seed")
    else:
        commit_all(repo, "seed")
        # A submodule commit that is not in this object store, as for any real submodule.
        git(repo, "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},a")
        git(repo, "commit", "-q", "-m", "gitlink")
    return repo


@READERS
@pytest.mark.parametrize("kind", ["gitlink", "symlink"])
@pytest.mark.parametrize("path", ["a/contract.yaml", "a/b/contract.yaml"])
def test_gitlink_or_symlink_ancestor_is_absent(reader: object, tmp_path: Path, kind: str, path: str) -> None:
    repo = _non_tree_ancestor_repo(tmp_path, kind)
    assert _outcome(reader, repo, path) == "absent"


@pytest.mark.parametrize("kind", ["gitlink", "symlink"])
def test_gitlink_or_symlink_ancestor_parity(tmp_path: Path, kind: str) -> None:
    repo = _non_tree_ancestor_repo(tmp_path, kind)
    assert _outcome(_per_spawn, repo, "a/b/contract.yaml") == _outcome(_batched, repo, "a/b/contract.yaml")


@READERS
@pytest.mark.parametrize("path", ["a/./b/contract.yaml", "a//b/contract.yaml", "a/../a/b/contract.yaml"])
def test_non_normalised_paths_are_absent_without_a_walk(
    reader: object, repo: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """Pin today's behaviour: git cannot resolve these spellings either, so they are absent and never walked.

    Safe because a contract locator that changes spelling is a locator change, which the C9 control-plane check
    reports; and an un-normalised spelling can never name a real entry, so there is nothing to erase.
    """

    def _no_walk(*_a: object, **_k: object) -> None:
        raise AssertionError("a non-normalised path must not walk ancestors")

    monkeypatch.setattr(_git_run, "_ls_tree_per_spawn", _no_walk)
    monkeypatch.setattr(_git_run, "_tree_entries", _no_walk)
    assert _outcome(reader, repo, path) == "absent"


# --- other leaf modes, malformed listings, sha256, Unicode normalisation ---------------------------------------

_LEAF_MODES = {"symlink": "120000", "executable": "100755", "regular": "100644"}
OBJECT_FORMATS = pytest.mark.parametrize("object_format", ["sha1", "sha256"])


def _mode_repo(root: Path, object_format: str = "sha1") -> Path:
    """A repo whose ``d/`` tree holds one leaf per mode, all built by plumbing; ``top.txt`` sits at the root."""
    repo = root / "modes"
    repo.mkdir(parents=True)
    _plumb(repo, "init", "-q", "-b", "main", f"--object-format={object_format}")
    entries = []
    for name, mode in _LEAF_MODES.items():
        blob = _plumb(repo, "hash-object", "-w", "--stdin", stdin=f"{name} payload\n".encode())
        entries.append((name, blob, "blob", mode))
    inner = _mktree(repo, entries)
    top = _plumb(repo, "hash-object", "-w", "--stdin", stdin=b"x\n")
    outer = _mktree(repo, [("d", inner, "tree", "040000"), ("top.txt", top, "blob", "100644")])
    _plumb(repo, "update-ref", "HEAD", _plumb(repo, "commit-tree", outer, "-m", "modes"))
    return repo


@READERS
@OBJECT_FORMATS
@pytest.mark.parametrize("leaf", list(_LEAF_MODES))
def test_leaf_of_any_mode_reads_when_intact(reader: object, tmp_path: Path, object_format: str, leaf: str) -> None:
    repo = _mode_repo(tmp_path, object_format)
    assert _outcome_native(reader, repo, f"d/{leaf}") == f"{leaf} payload\n".encode()
    assert _outcome_native(reader, repo, f"d/{leaf}/x") == "absent"  # a file used as a directory
    assert _outcome_native(reader, repo, "d/nothing") == "absent"


@READERS
@OBJECT_FORMATS
@pytest.mark.parametrize("leaf", list(_LEAF_MODES))
def test_leaf_of_any_mode_with_deleted_object_fails_closed(
    reader: object, tmp_path: Path, object_format: str, leaf: str
) -> None:
    repo = _mode_repo(tmp_path, object_format)
    _delete_object(repo, f"HEAD:d/{leaf}")
    assert _outcome_native(reader, repo, f"d/{leaf}") == "unreadable"
    for other in set(_LEAF_MODES) - {leaf}:  # the neighbours in the same tree are unaffected
        assert _outcome_native(reader, repo, f"d/{other}") == f"{other} payload\n".encode()
    assert _outcome_native(reader, repo, "d/nothing") == "absent"


@READERS
@OBJECT_FORMATS
def test_sha256_deleted_trees_and_absent(reader: object, tmp_path: Path, object_format: str) -> None:
    repo = _mode_repo(tmp_path, object_format)
    assert _plumb(repo, "rev-parse", "--show-object-format") == object_format
    assert _outcome_native(reader, repo, "top.txt") == b"x\n"
    assert _outcome_native(reader, repo, "absent.txt") == "absent"
    _delete_object(repo, "HEAD:d")
    assert _outcome_native(reader, repo, "d/regular") == "unreadable"
    assert _outcome_native(reader, repo, "d/nothing") == "unreadable"
    assert _outcome_native(reader, repo, "top.txt") == b"x\n"
    assert _outcome_native(reader, repo, "absent.txt") == "absent"


@OBJECT_FORMATS
@pytest.mark.parametrize("erase", [None, "HEAD:d", "HEAD:d/symlink", "HEAD:d/executable"])
@pytest.mark.parametrize("path", ["d/symlink", "d/executable", "d/regular", "d/nothing", "top.txt", "zz/x"])
def test_mode_repo_readers_agree(tmp_path: Path, object_format: str, erase: str | None, path: str) -> None:
    repo = _mode_repo(tmp_path, object_format)
    if erase is not None:
        _delete_object(repo, erase)
    assert _outcome(_per_spawn, repo, path) == _outcome(_batched, repo, path)


@pytest.mark.parametrize("hash_len", [20, 32])
@pytest.mark.parametrize(
    "body",
    [b"100644 name", b"100644 name\0", b"100644name", b"\0" * 5, b"garbage"],
    ids=["no-nul", "nul-no-hash", "no-space", "nuls", "garbage"],
)
def test_tree_entries_rejects_malformed_bodies(body: bytes, hash_len: int) -> None:
    """``_tree_entries`` is the batch path's tree parser: malformed input is ``None``, never a partial dict."""
    assert _git_run._tree_entries(body, hash_len) is None


@pytest.mark.parametrize("hash_len", [20, 32])
def test_tree_entries_rejects_a_truncated_hash_and_keeps_whole_records(hash_len: int) -> None:
    whole = b"100644 a\0" + b"\1" * hash_len
    assert _git_run._tree_entries(whole, hash_len) == {b"a": b"100644"}
    assert _git_run._tree_entries(whole[:-1], hash_len) is None
    assert _git_run._tree_entries(whole + b"40000 b\0" + b"\2" * (hash_len - 1), hash_len) is None


_MALFORMED_LISTINGS = {
    "truncated-hash": lambda oid: b"100644 contract.yaml\0" + b"\1" * 7,
    "no-nul": lambda oid: b"100644 contract.yaml",
    "garbage": lambda oid: b"not a tree at all",
}


@pytest.mark.parametrize("bad", list(_MALFORMED_LISTINGS))
@pytest.mark.parametrize("erase_leaf", [True, False], ids=["leaf-deleted", "leaf-intact"])
def test_malformed_tree_listing_falls_back_to_per_spawn(
    repo: Path, monkeypatch: pytest.MonkeyPatch, bad: str, erase_leaf: bool
) -> None:
    """Drives ``GitBlobBatch._classify_missing`` (via ``read_blob``) with ``_query`` answering a ``<rev>:<dir>``
    tree request with a malformed body. The batch must latch ``_broken`` and hand the read to the per-spawn
    path, whose own answer stands: unreadable for a deleted leaf, never a batch-invented ``None``.
    """
    path = "a/b/contract.yaml"
    if erase_leaf:
        _delete_object(repo, f"HEAD:{path}")
    real_query = _git_run.GitBlobBatch._query
    poisoned: list[str] = []

    def _query(self: _git_run.GitBlobBatch, spec: str) -> tuple[str, bytes, str] | None:
        answer = real_query(self, spec)
        if answer is not None and answer[0] == "tree":
            poisoned.append(spec)
            return ("tree", _MALFORMED_LISTINGS[bad](answer[2]), answer[2])
        return answer

    fallbacks: list[str] = []
    real_spawn = _git_run._read_blob_per_spawn

    def _spy(repo_root: Path, rev: str, p: str) -> bytes | None:
        fallbacks.append(p)
        return real_spawn(repo_root, rev, p)

    monkeypatch.setattr(_git_run.GitBlobBatch, "_query", _query)
    monkeypatch.setattr(_git_run, "_read_blob_per_spawn", _spy)
    with blob_batch(repo) as batch:
        if erase_leaf:
            with pytest.raises(BlobUnreadable):
                read_blob(repo, "HEAD", path)
        else:  # an intact leaf never asks for a listing: the batch answers itself
            assert read_blob(repo, "HEAD", path) == b"rules: strict\n"
        assert bool(poisoned) is erase_leaf
        assert batch._broken is erase_leaf
        assert fallbacks == ([path] if erase_leaf else [])
        if erase_leaf:  # latched: the next read is served per-spawn too
            assert read_blob(repo, "HEAD", "top.txt") == b"x\n"
            assert fallbacks == [path, "top.txt"]


@pytest.mark.parametrize("bad", list(_MALFORMED_LISTINGS))
def test_malformed_listing_over_a_deleted_tree_still_raises_via_per_spawn(
    repo: Path, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    real_query = _git_run.GitBlobBatch._query
    poisoned: list[str] = []

    def _query(self: _git_run.GitBlobBatch, spec: str) -> tuple[str, bytes, str] | None:
        answer = real_query(self, spec)
        if answer is not None and answer[0] == "tree":
            poisoned.append(spec)
            return ("tree", _MALFORMED_LISTINGS[bad](answer[2]), answer[2])
        return answer

    monkeypatch.setattr(_git_run.GitBlobBatch, "_query", _query)
    _delete_object(repo, "HEAD:a/b")
    with blob_batch(repo) as batch:
        with pytest.raises(BlobUnreadable):  # the deleted tree is still seen: per-spawn walks it itself
            read_blob(repo, "HEAD", "a/b/other.yaml")
        assert poisoned, "the malformed listing must actually have been served"
        assert batch._broken is True
