"""Swap-window tests: uninstall deletion sites re-check at the act, not only at the check.

Each site checks ``path_refusal`` and then deletes. An interloper replaces the
planned plain file with a symlink to user bytes BETWEEN the two. The link is not
TRW's content, so it must survive, the outside bytes must survive, and the site
must report a refusal (tests/AGENTS.md "Changes that delete ... user files", 1 and 9).
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import pytest

from trw_mcp.bootstrap import _safe_remove, _template_updater, _uninstall_manifest
from trw_mcp.bootstrap._uninstall_manifest import KeyDisposition, apply_removal
from trw_mcp.server import _uninstall_corpus


@pytest.fixture
def layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside" / "user.txt"
    outside.parent.mkdir()
    outside.write_text("user bytes", encoding="utf-8")
    return root, outside, tmp_path


def _arm_swap(monkeypatch: pytest.MonkeyPatch, target: Path, outside: Path, *modules: object) -> list[bool]:
    """After the first ``path_refusal`` verdict on *target*, swap it for a symlink to *outside*."""
    real = _safe_remove.path_refusal
    swapped: list[bool] = []

    def check_then_swap(path: Path, root: Path) -> str | None:
        verdict = real(path, root)
        if path == target and not swapped:
            target.unlink()
            target.symlink_to(outside)
            swapped.append(True)
        return verdict

    for module in modules:
        monkeypatch.setattr(module, "path_refusal", check_then_swap)
    return swapped


#: What the manifest records for the "trw bytes" victims: real dispositions always carry the planned hash.
_TRW_HASH = hashlib.sha256(b"trw bytes").hexdigest()


def _assert_survived(link: Path, outside: Path, expected: str = "user bytes") -> None:
    assert link.is_symlink(), "the interloper's symlink was unlinked"
    assert outside.read_text(encoding="utf-8") == expected


def test_apply_removal_does_not_unlink_a_file_swapped_for_a_symlink(
    layout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, outside, _ = layout
    victim = root / ".claude" / "thing.md"
    victim.parent.mkdir()
    victim.write_text("trw bytes", encoding="utf-8")
    swapped = _arm_swap(monkeypatch, victim, outside, _uninstall_manifest, _safe_remove)
    result: dict[str, list[str]] = {}
    removed, errors = apply_removal([KeyDisposition("k", victim, "remove", recorded_hash=_TRW_HASH)], result, root)
    assert swapped == [True]
    assert removed == set()  # a failed removal keeps its manifest record
    assert errors == 1
    assert result["errors"]
    _assert_survived(victim, outside)


def test_retired_hook_withdrawal_does_not_unlink_a_file_swapped_for_a_symlink(
    layout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, outside, _ = layout
    hook = root / ".claude" / "hooks" / "old.sh"
    hook.parent.mkdir(parents=True)
    hook.write_text("#!/bin/sh\n", encoding="utf-8")
    outside.write_text("#!/bin/sh\n", encoding="utf-8")  # same bytes: the hash match must not reach an unlink
    recorded = hashlib.sha256(hook.read_bytes()).hexdigest()
    swapped = _arm_swap(monkeypatch, hook, outside, _template_updater, _safe_remove)
    result: dict[str, list[str]] = {}
    _template_updater._withdraw_retired_hooks(root, set(), {"old.sh": recorded}, result)
    assert swapped == [True]
    assert "removed" not in result
    _assert_survived(hook, outside, "#!/bin/sh\n")


def test_keep_memory_cleanup_does_not_unlink_a_file_swapped_for_a_symlink(
    layout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, outside, _ = layout
    trw = root / ".trw"
    trw.mkdir()
    victim = trw / "session.json"
    victim.write_text("{}", encoding="utf-8")
    swapped = _arm_swap(monkeypatch, victim, outside, _safe_remove)
    removed, errors = _uninstall_corpus.keep_memory_in_dir(trw, root, lambda p, _t: str(p))
    assert swapped == [True]
    assert (removed, errors) == (0, 1)
    assert "Error removing" in capsys.readouterr().out
    _assert_survived(victim, outside)


# ---------------------------------------------------------------------------
# Act-time race, dangling symlink, failed reads, whole-tree bytes
# ---------------------------------------------------------------------------


class Site(NamedTuple):
    """One deletion site: builds the victim, runs the site, reports whether it deleted or reported."""

    build: Callable[[Path, Path], Path]
    run: Callable[[Path, Path], tuple[bool, list[str]]]  # (deleted_anything, reported messages)


def _build_manifest(root: Path, outside: Path) -> Path:
    victim = root / ".claude" / "thing.md"
    victim.parent.mkdir()
    victim.write_text("trw bytes", encoding="utf-8")
    return victim


def _run_manifest(root: Path, victim: Path) -> tuple[bool, list[str]]:
    result: dict[str, list[str]] = {}
    removed, _errors = apply_removal([KeyDisposition("k", victim, "remove", recorded_hash=_TRW_HASH)], result, root)
    return bool(removed), result.get("errors", [])


def _build_hook(root: Path, outside: Path) -> Path:
    victim = root / ".claude" / "hooks" / "old.sh"
    victim.parent.mkdir(parents=True)
    victim.write_text("#!/bin/sh\n", encoding="utf-8")
    return victim


def _run_hook(root: Path, victim: Path) -> tuple[bool, list[str]]:
    recorded = hashlib.sha256(b"#!/bin/sh\n").hexdigest()
    result: dict[str, list[str]] = {}
    _template_updater._withdraw_retired_hooks(root, set(), {"old.sh": recorded}, result)
    return "removed" in result, result.get("warnings", [])


def _build_corpus(root: Path, outside: Path) -> Path:
    victim = root / ".trw" / "session.json"
    victim.parent.mkdir()
    victim.write_text("{}", encoding="utf-8")
    return victim


def _run_corpus(root: Path, victim: Path) -> tuple[bool, list[str]]:
    removed, _errors = _uninstall_corpus.keep_memory_in_dir(root / ".trw", root, lambda p, _t: str(p))
    return removed > 0, []


SITES = {
    # apply_removal no longer calls safe_remove for a hash-recorded file: it goes through remove_if_hash,
    # whose act-time hazards are covered in test_uninstall_remove_if_hash.py.
    # retired_hook no longer calls safe_remove: it goes through remove_if_hash, whose act-time
    # hazards (symlink/dir/replace swap, failed read) are covered in test_hook_withdraw_safe.py.
    "keep_memory": Site(_build_corpus, _run_corpus),
}


def _swap_at_act(monkeypatch: pytest.MonkeyPatch, victim: Path, outside: Path, kind: str) -> list[bool]:
    """Swap *victim* just before safe_remove's own final lstat (its LAST lstat call on the victim)."""
    real = _safe_remove._lstat_mode
    seen: list[bool] = []
    swapped: list[bool] = []

    def lstat_then_swap(path: Path) -> int | None:
        if path == victim and not swapped:
            seen.append(True)
            # safe_remove's own path_refusal has already run when its act-time lstat is reached: the
            # act lstat is the call after the refusal's symlink check, i.e. the 2nd call from safe_remove.
            if len(seen) == _ACT_CALL[0]:
                if kind == "symlink":
                    victim.unlink()
                    victim.symlink_to(outside)
                elif kind == "dir":
                    victim.unlink()
                    victim.mkdir()
                    (victim / "user.txt").write_text("user dir bytes", encoding="utf-8")
                else:
                    staging = victim.parent / "racer.tmp"
                    staging.write_text("racer bytes", encoding="utf-8")
                    os.replace(staging, victim)
                swapped.append(True)
        return real(path)

    monkeypatch.setattr(_safe_remove, "_lstat_mode", lstat_then_swap)
    return swapped


_ACT_CALL = [2]


@pytest.mark.parametrize("name", sorted(SITES))
def test_swap_after_safe_removes_own_check_symlink_is_not_unlinked(
    name: str, layout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, outside, _ = layout
    site = SITES[name]
    victim = site.build(root, outside)
    _ACT_CALL[0] = 2
    swapped = _swap_at_act(monkeypatch, victim, outside, "symlink")
    deleted, _messages = site.run(root, victim)
    assert swapped == [True]
    assert not deleted
    _assert_survived(victim, outside)


@pytest.mark.parametrize("name", sorted(SITES))
def test_swap_after_safe_removes_own_check_atomic_replace_removes_the_named_path(
    name: str, layout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An atomic replace by a different regular file is NOT detectable by a symlink re-lstat.

    Expected, by design: the named path now holds the racer's regular file and safe_remove removes the
    named path (it never claims byte-level ownership; the hash proof is the caller's, taken earlier).
    What must hold is that nothing OUTSIDE the named path is touched.
    """
    root, outside, _ = layout
    site = SITES[name]
    victim = site.build(root, outside)
    _ACT_CALL[0] = 2
    swapped = _swap_at_act(monkeypatch, victim, outside, "replace")
    site.run(root, victim)
    assert swapped == [True]
    assert not victim.exists()
    assert outside.read_text(encoding="utf-8") == "user bytes"


def test_apply_removal_dangling_symlink_swap_keeps_the_record_and_reports(
    layout: tuple[Path, Path, Path],
) -> None:
    root, outside, tmp = layout
    victim = root / ".claude" / "thing.md"
    victim.parent.mkdir()
    victim.symlink_to(tmp / "nowhere")  # dangling: is_file() is False, so the old guard skipped it
    result: dict[str, list[str]] = {}
    removed, errors = apply_removal([KeyDisposition("k", victim, "remove", recorded_hash=_TRW_HASH)], result, root)
    assert removed == set()
    assert errors == 1
    assert str(victim) in result["errors"][0]
    assert victim.is_symlink()
    assert outside.read_text(encoding="utf-8") == "user bytes"


def test_apply_removal_absent_target_drops_the_record(layout: tuple[Path, Path, Path]) -> None:
    root, outside, _ = layout
    victim = root / ".claude" / "gone.md"
    victim.parent.mkdir()
    result: dict[str, list[str]] = {}
    removed, errors = apply_removal([KeyDisposition("k", victim, "remove", recorded_hash=_TRW_HASH)], result, root)
    assert (removed, errors) == ({"k"}, 0)
    assert outside.read_text(encoding="utf-8") == "user bytes"


@pytest.mark.parametrize("name", sorted(SITES))
@pytest.mark.parametrize("failing_from", [1, 2, 3])
def test_a_failed_read_on_the_victim_preserves_it(
    name: str, failing_from: int, layout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """PermissionError on the Nth and every later lstat of the victim: no delete, never a crash.

    Sticky rather than one-shot: ``Path.resolve`` swallows a single failed lstat, so a one-shot fault at
    call 2 would be absorbed and prove nothing.
    """
    root, outside, _ = layout
    site = SITES[name]
    victim = site.build(root, outside)
    real = os.lstat
    calls: list[bool] = []

    def flaky(path: str | os.PathLike[str], *args: object, **kwargs: object) -> os.stat_result:
        if Path(path) == victim:
            calls.append(True)
            if len(calls) >= failing_from:
                raise PermissionError(13, "denied")
        return real(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "lstat", flaky)
    deleted, _messages = site.run(root, victim)
    assert len(calls) >= failing_from
    assert not deleted
    assert victim.is_file()
    assert outside.read_text(encoding="utf-8") == "user bytes"


def test_unreadable_hook_dir_warns_and_deletes_nothing(
    layout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every lstat under the hooks dir raises PermissionError (portable stand-in for chmod 0, root-proof)."""
    root, outside, _ = layout
    victim = _build_hook(root, outside)
    hooks = victim.parent
    real = os.lstat

    def denied(path: str | os.PathLike[str], *args: object, **kwargs: object) -> os.stat_result:
        if Path(path) == hooks or hooks in Path(path).parents:
            raise PermissionError(13, "denied")
        return real(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "lstat", denied)
    deleted, messages = _run_hook(root, victim)
    assert not deleted
    assert messages
    monkeypatch.undo()
    assert victim.is_file()
    assert outside.read_text(encoding="utf-8") == "user bytes"


def test_uninstall_prints_a_named_error_line_for_a_refused_removal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: a safe_remove refusal is printed with the file's path, not only counted."""
    import argparse

    from trw_mcp.bootstrap import init_project
    from trw_mcp.server import _subcommands_lifecycle as lifecycle

    (tmp_path / ".git").mkdir()
    assert not init_project(tmp_path, ide="claude-code")["errors"]
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("user bytes", encoding="utf-8")
    swapped: list[Path] = []
    real_apply = apply_removal

    def swap_then_apply(dispositions: list[KeyDisposition], result: dict[str, list[str]], target: Path, **kw: bool):  # type: ignore[no-untyped-def]
        victim = next(d.path for d in dispositions if d.action == "remove" and not d.is_dir)
        victim.unlink()
        victim.symlink_to(outside)
        swapped.append(victim)
        return real_apply(dispositions, result, target, **kw)

    monkeypatch.setattr(_uninstall_manifest, "apply_removal", swap_then_apply)
    with pytest.raises(SystemExit):
        lifecycle._run_uninstall(
            argparse.Namespace(
                target_dir=str(tmp_path), dry_run=False, yes=True, delete_memory=False, keep_memory=False
            )
        )
    out = capsys.readouterr().out
    assert len(swapped) == 1
    assert f"  Error: {swapped[0]}: refused: path is a symlink" in out
    assert swapped[0].is_symlink()
    assert outside.read_text(encoding="utf-8") == "user bytes"


@pytest.mark.parametrize("name", sorted(SITES))
def test_file_swapped_for_a_directory_at_the_act_is_refused_not_rmtreed(
    name: str, layout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file site must never escalate to a recursive delete of a directory swapped in at the act."""
    root, outside, _ = layout
    site = SITES[name]
    victim = site.build(root, outside)
    _ACT_CALL[0] = 2
    swapped = _swap_at_act(monkeypatch, victim, outside, "dir")
    deleted, _messages = site.run(root, victim)
    assert swapped == [True]
    assert not deleted
    assert (victim / "user.txt").read_text(encoding="utf-8") == "user dir bytes"
    assert outside.read_text(encoding="utf-8") == "user bytes"


def test_keep_memory_directory_child_swapped_for_a_file_is_refused(
    layout: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reverse: a planned directory replaced by a file at the act is kept, not unlinked."""
    root, outside, _ = layout
    trw = root / ".trw"
    victim = trw / "scratch"
    victim.mkdir(parents=True)
    (victim / "a.txt").write_text("a", encoding="utf-8")
    real = _safe_remove._lstat_mode
    seen: list[bool] = []

    def swap(path: Path) -> int | None:
        if path == victim:
            seen.append(True)
            if len(seen) == 2:
                for child in victim.iterdir():
                    child.unlink()
                victim.rmdir()
                victim.write_text("racer file", encoding="utf-8")
        return real(path)

    monkeypatch.setattr(_safe_remove, "_lstat_mode", swap)
    removed, errors = _uninstall_corpus.keep_memory_in_dir(trw, root, lambda p, _t: str(p))
    assert (removed, errors) == (0, 1)
    assert "expected a directory, found a file" in capsys.readouterr().out
    assert victim.read_text(encoding="utf-8") == "racer file"
    assert outside.read_text(encoding="utf-8") == "user bytes"


@pytest.mark.parametrize(
    ("expect", "kind", "refused"),
    [
        ("file", "file", False),
        ("file", "dir", True),
        ("dir", "dir", False),
        ("dir", "file", True),
        ("any", "file", False),
        ("any", "dir", False),
    ],
)
def test_safe_remove_expect_table(tmp_path: Path, expect: str, kind: str, refused: bool) -> None:
    root = tmp_path / "p"
    root.mkdir()
    target = root / "t"
    if kind == "dir":
        target.mkdir()
        (target / "f").write_text("x", encoding="utf-8")
    else:
        target.write_text("x", encoding="utf-8")
    reason = _safe_remove.safe_remove(target, root, expect=expect)  # type: ignore[arg-type]
    assert (reason is not None) is refused
    assert target.exists() is refused
