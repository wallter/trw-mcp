"""Off macOS an explicit uninstall deletes only captures it can prove are TRW's own unchanged bytes (HB-2).

The proof is the ``sha256`` in ``meta.json`` matching the captured ``data``, a regular file with no other name.
Anything else stays in ``.trw/trash`` and the output says why. Runs on every platform; the uninstall report
tests force the non-darwin branch.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

_TRW = b"trw bytes\n"
_SHA = hashlib.sha256(_TRW).hexdigest()


def _capture(tmp_path: Path, name: str = "thing.md") -> tuple[Path, Path]:
    from trw_mcp.bootstrap._safe_remove import remove_if_hash

    root = tmp_path / "proj"
    victim = root / ".claude" / name
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_bytes(_TRW)
    outcome = remove_if_hash(victim, root, _SHA)
    assert outcome.status == "removed" and outcome.retained_at is not None
    return root, outcome.retained_at


def test_capture_meta_records_the_hash_trw_wrote(tmp_path: Path) -> None:
    _root, data = _capture(tmp_path)
    assert json.loads((data.parent / "meta.json").read_text())["sha256"] == _SHA


def test_an_unchanged_capture_is_deleted_with_its_folder(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._safe_remove import delete_proven_unchanged_captures

    root, data = _capture(tmp_path)
    deleted, kept = delete_proven_unchanged_captures(root, [data])
    assert (deleted, kept) == (1, [])
    assert list((root / ".trw" / "trash").iterdir()) == []


@pytest.mark.parametrize(
    ("mutate", "why"),
    [
        (lambda d: d.write_bytes(b"user edit\n"), "changed since TRW wrote it"),
        (lambda d: (d.parent / "meta.json").unlink(), "could not prove it unchanged"),
        (lambda d: (d.parent / "meta.json").write_text("{}"), "no recorded hash"),
        (lambda d: (d.parent / "meta.json").write_text("not json"), "could not prove it unchanged"),
        (lambda d: (d.parent / "meta.json").write_text("[" * 20000), "could not prove it unchanged"),
        (lambda d: os.link(d, d.parent / "other-name"), "another name refers to the same bytes"),
    ],
    ids=["edited", "no-meta", "no-hash", "bad-json", "deep-json", "second-link"],
)
def test_an_unproven_capture_is_kept_with_a_reason(tmp_path: Path, mutate: object, why: str) -> None:
    from trw_mcp.bootstrap._safe_remove import delete_proven_unchanged_captures

    root, data = _capture(tmp_path)
    mutate(data)  # type: ignore[operator]
    before = {p.name: p.read_bytes() for p in data.parent.iterdir()}
    deleted, kept = delete_proven_unchanged_captures(root, [data])
    assert deleted == 0 and len(kept) == 1 and kept[0][0] == data and why in kept[0][1]
    assert {p.name: p.read_bytes() for p in data.parent.iterdir()} == before


def test_a_capture_written_after_the_hash_check_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A writer that lands between the hash check and the unlink is seen by the pre-unlink stat re-check."""
    from trw_mcp.bootstrap import _trash_purge
    from trw_mcp.bootstrap._safe_remove import delete_proven_unchanged_captures

    root, data = _capture(tmp_path)
    real = _trash_purge._sha256_stable

    def then_a_late_write(fd: int, expected: str, cfd: int) -> bool:
        ok = real(fd, expected, cfd)
        data.write_bytes(_TRW + b"late\n")  # same inode, after the proof
        return ok

    monkeypatch.setattr(_trash_purge, "_sha256_stable", then_a_late_write)
    deleted, kept = delete_proven_unchanged_captures(root, [data])
    assert deleted == 0 and kept and "changed while being checked" in kept[0][1]
    assert data.read_bytes() == _TRW + b"late\n"


def test_a_symlinked_data_is_kept(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._safe_remove import delete_proven_unchanged_captures

    root, data = _capture(tmp_path)
    target = tmp_path / "user-file"
    target.write_bytes(_TRW)
    data.unlink()
    data.symlink_to(target)
    deleted, kept = delete_proven_unchanged_captures(root, [data])
    assert deleted == 0 and kept and target.read_bytes() == _TRW


def test_a_path_outside_the_trash_is_never_touched(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._safe_remove import delete_proven_unchanged_captures

    root, _data = _capture(tmp_path)
    stray = root / "notes" / "data"
    stray.parent.mkdir()
    stray.write_bytes(_TRW)
    deleted, kept = delete_proven_unchanged_captures(root, [stray])
    assert deleted == 0 and kept == [(stray, "not a TRW capture folder")] and stray.read_bytes() == _TRW


def test_uninstall_off_macos_deletes_proven_captures_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.server import _uninstall_trash_report as report

    root, data = _capture(tmp_path)
    _root, edited = _capture(tmp_path, "edited.md")
    edited.write_bytes(b"user edit\n")
    monkeypatch.setattr(report.sys, "platform", "linux")
    result = {
        "trashed": [str(root / ".claude" / "thing.md"), str(root / ".claude" / "edited.md")],
        "trashed_at": [str(data), str(edited)],
    }
    report._move_matched_captures_to_os_trash(result, root, lambda p, r: str(p.relative_to(r)))
    out = capsys.readouterr().out
    assert "  Removed 1 unchanged TRW file(s)" in out
    remove = f"remove with: rm -rf {root / '.trw' / 'trash'}"
    assert f"Kept in .trw/trash: .claude/edited.md (changed since TRW wrote it; see doctor; {remove})" in out
    assert not data.parent.exists() and edited.read_bytes() == b"user edit\n"


def test_uninstall_on_macos_names_why_a_capture_stayed_in_trw_trash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """E2E-INC-062: with no usable ~/.Trash the captures stay, and each line says why (not only 'see doctor')."""
    from trw_mcp.server import _uninstall_trash_report as report

    root, data = _capture(tmp_path)
    monkeypatch.setattr(report.sys, "platform", "darwin")
    monkeypatch.setattr(
        report,
        "move_captures_to_os_trash",
        lambda _root, paths: (None, [(p, "no usable system Trash (FileNotFoundError): ~/.Trash") for p in paths]),
    )
    result = {"trashed": [str(root / ".claude" / "thing.md")], "trashed_at": [str(data)]}
    report._move_matched_captures_to_os_trash(result, root, lambda p, r: str(p.relative_to(r)))
    out = capsys.readouterr().out
    assert (
        "Kept in .trw/trash: .claude/thing.md (no usable system Trash (FileNotFoundError): ~/.Trash; see doctor; "
        f"remove with: rm -rf {root / '.trw' / 'trash'})" in out
    )
    assert data.read_bytes() == _TRW
