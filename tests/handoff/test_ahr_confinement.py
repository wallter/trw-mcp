"""P0: ``file:`` URIs from a record are untrusted (R-SEC-1/R-SEC-2) and confined to the repository.

``check`` opens a pointer only when it names a relative path that stays inside the repository root
after symlinks resolve; everything else is ``not_accessed`` with a reason and is never hashed.
``new`` refuses ``--next-read`` paths outside the root, so a record never carries a home path.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from tests.handoff._cli_support import _check, _filled_sealed, _new, _raw, _run, repo  # noqa: F401
from trw_mcp.handoff import load
from trw_mcp.handoff._repo import confined_path, raw_digest

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


@pytest.mark.parametrize(
    ("uri", "reason"),
    [
        ("file:/etc/passwd", "absolute"),
        ("file:///etc/passwd", "absolute"),
        ("file://evil.example/etc/passwd", "absolute"),
        ("file:../outside.txt", "parent-directory"),
        ("file:sub/../../outside.txt", "parent-directory"),
        ("file:%2e%2e/outside.txt", "percent-encoded dot"),
        ("file:%2E%2E%2Foutside.txt", "percent-encoded dot"),
        ("file:a%00b", "control character"),
        ("file:", "empty path"),
        ("https://example.com/x", "never fetched"),
    ],
)
def test_confined_path_refuses_escapes(tmp_path: Path, uri: str, reason: str) -> None:
    path, why = confined_path(uri, tmp_path)
    assert path is None and reason in why


def test_confined_path_refuses_a_symlink_that_leaves_the_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("s\n", encoding="utf-8")
    (root / "link.txt").symlink_to(tmp_path / "secret.txt")
    path, why = confined_path("file:link.txt", root)
    assert path is None and "outside the repository" in why
    inside, _ = confined_path("file:sub/x.txt", root)
    assert inside == (root / "sub" / "x.txt").resolve()


def _point_at(path: Path, uri: str, digest: str | None = "sha256:" + "0" * 64) -> None:
    doc = load(path)
    doc["next_read"][0] = {"uri": uri, "why": "test"} | ({"digest": digest} if digest else {})
    doc.pop("integrity", None)  # minimal may be unsealed
    path.write_text(json.dumps(doc), encoding="utf-8")


@pytest.mark.parametrize("uri", ["file:../outside.txt", "file:%2e%2e/outside.txt", "OUTSIDE_ABS", "file:link.txt"])
def test_check_never_opens_a_pointer_outside_the_repository(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, uri: str
) -> None:
    outside = repo.parent / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    (repo / "link.txt").symlink_to(outside)
    path, _ = _filled_sealed(capsys, "--next-read", "a.txt")
    _point_at(path, "file:" + str(outside) if uri == "OUTSIDE_ABS" else uri, _raw(outside))
    opened: list[str] = []
    real_open = os.open
    with monkeypatch.context() as patch:
        patch.setattr(os, "open", lambda p, *a, **k: opened.append(str(p)) or real_open(p, *a, **k))
        _, report = _check(capsys, path)
    pc = report["pointer_checks"][0]
    assert pc["status"] == "not_accessed" and pc["reason"] and "observed_digest" not in pc
    assert not any("outside.txt" in p for p in opened)


def test_no_digest_pointer_is_not_hashed(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _ = _filled_sealed(capsys, "--next-read", "a.txt")
    _point_at(path, "file:a.txt", None)
    monkeypatch.setattr("trw_mcp.handoff._check.raw_digest", lambda _p: pytest.fail("hashed a no_digest pointer"))
    assert _check(capsys, path)[1]["pointer_checks"] == [{"index": 0, "status": "no_digest"}]


def test_raw_digest_stats_before_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A FIFO (or any non-regular file) is refused by ``os.stat`` before ``open`` is ever called."""
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    opened: list[object] = []
    with monkeypatch.context() as patch, pytest.raises(OSError, match="not a regular file"):
        patch.setattr(os, "open", lambda *a, **_k: opened.append(a) or -1)
        raw_digest(fifo)
    assert opened == []


def test_changed_paths_sidecar_outside_the_repository_is_not_read(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    path, _ = _filled_sealed(capsys, "--tier", "standard", "--next-read", "a.txt")
    doc = load(path)
    doc["as_of"]["base_ref"]["changed_paths"]["uri"] = "file:../elsewhere.txt"
    (repo.parent / "elsewhere.txt").write_text("a.txt\n", encoding="utf-8")
    doc.pop("integrity")
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert _check(capsys, path)[1]["git"]["changed_paths"] == "not_checked"


def test_new_refuses_next_read_outside_the_root(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outside = repo.parent / "home-note.txt"
    outside.write_text("x\n", encoding="utf-8")
    for spec in (str(outside), "file:../home-note.txt"):
        code, _, err = _run(capsys, "new", "--subject", "s", "--next-read", spec)
        assert code == 2 and "outside the repository" in err


def test_new_accepts_a_file_uri_prefix(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ptr = load(_new(capsys, "--subject", "s", "--next-read", "file:a.txt"))["next_read"][0]
    assert ptr["uri"] == "file:a.txt" and ptr["digest"] == _raw(repo / "a.txt")


def test_new_refuses_a_symlinked_out_path(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = repo / "target.json"
    (repo / "out.json").symlink_to(target)
    code, _, err = _run(capsys, "new", "--subject", "s", "--out", "out.json")
    assert code == 2 and "symlink" in err and not target.exists()
