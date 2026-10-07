"""FB-INSTALL-01: update-project keeps the hook family coherent when a shared lib was edited.

It used to keep an edited ``lib-trw.sh`` (an older 588-line copy) while replacing every hook that sources
it, so ~20 functions were undefined and each hook silently exited 0. Now the edited lib is replaced in place
when git holds it clean (the report names the restore command), or backed up to ``.trw/trash`` (named in the
report) when it has uncommitted edits, and refreshed together with its dependents; if it cannot be backed up,
the lib AND every hook that sources it are left as they were, so the old family stays whole.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401

pytestmark = pytest.mark.integration

_OLD_LIB = "#!/bin/sh\n# an older lib-trw.sh\nold_helper() { :; }\n"
_OLD_HOOK = '#!/bin/sh\n. "$(dirname "$0")/lib-trw.sh"\nold_helper\n'


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _old_family(repo: Path) -> tuple[Path, Path, dict[str, str]]:
    """An install whose lib the user edited and whose session-start.sh is TRW's own (recorded) old copy."""
    hooks = repo / ".claude" / "hooks"
    lib, hook = hooks / "lib-trw.sh", hooks / "session-start.sh"
    lib.write_text(_OLD_LIB + "# my local tweak\n", encoding="utf-8")  # differs from what TRW recorded: edited
    hook.write_text(_OLD_HOOK, encoding="utf-8")
    manifest = {"lib-trw.sh": _sha(_OLD_LIB), "session-start.sh": _sha(_OLD_HOOK)}
    return lib, hook, manifest


def _update(repo: Path, manifest: dict[str, str]) -> dict[str, list[str]]:
    from trw_mcp.bootstrap._template_updater import _update_hooks
    from trw_mcp.bootstrap._utils import _DATA_DIR

    result: dict[str, list[str]] = {"updated": [], "created": [], "errors": [], "modified": []}
    _update_hooks(repo, _DATA_DIR, result, manifest_hashes=manifest, ide="claude-code")
    return result


def _bundled(name: str) -> str:
    from trw_mcp.bootstrap._utils import _DATA_DIR

    return (_DATA_DIR / "hooks" / name).read_text(encoding="utf-8")


def test_an_edited_lib_is_backed_up_and_refreshed_with_its_hooks(initialized_repo: Path) -> None:
    lib, hook, manifest = _old_family(initialized_repo)
    edited = lib.read_bytes()

    result = _update(initialized_repo, manifest)

    assert lib.read_text(encoding="utf-8") == _bundled("lib-trw.sh")
    assert hook.read_text(encoding="utf-8") == _bundled("session-start.sh")
    backups = [p for p in (initialized_repo / ".trw" / "trash").rglob("*") if p.is_file() and p.read_bytes() == edited]
    assert backups, "the user's edited lib must survive, byte for byte, in .trw/trash"
    notes = " ".join(result.get("warnings", []))
    assert "lib-trw.sh" in notes and ".trw/trash" in notes
    assert ".claude/hooks/lib-trw.sh" in result.get("retired", []), "or the dirty-file restore puts the old lib back"

    from trw_mcp.server._doctor_hook_family import hook_family_row

    assert hook_family_row(initialized_repo)[0] == "PASS"


def test_a_lib_that_cannot_be_backed_up_holds_its_whole_family(
    initialized_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap import _hook_family
    from trw_mcp.bootstrap._trash import Removal

    lib, hook, manifest = _old_family(initialized_repo)
    edited = lib.read_bytes()

    def cannot_capture(path: Path, root: Path, sha: str, *, key: str | None = None) -> Removal:
        return Removal(key, path, "kept", path, None, "capture refused for the test")

    monkeypatch.setattr(_hook_family, "remove_if_hash", cannot_capture)

    result = _update(initialized_repo, manifest)

    assert lib.read_bytes() == edited, "the user's lib is never overwritten when it cannot be backed up"
    assert hook.read_text(encoding="utf-8") == _OLD_HOOK, "a hook that sources the held lib must not be replaced"
    notes = " ".join(result.get("warnings", []))
    assert "lib-trw.sh" in notes and "session-start.sh" in notes and "capture refused for the test" in notes


def test_an_unedited_lib_updates_as_before(initialized_repo: Path) -> None:
    hooks = initialized_repo / ".claude" / "hooks"
    (hooks / "lib-trw.sh").write_text(_OLD_LIB, encoding="utf-8")  # exactly what TRW recorded: not edited

    result = _update(initialized_repo, {"lib-trw.sh": _sha(_OLD_LIB)})

    assert (hooks / "lib-trw.sh").read_text(encoding="utf-8") == _bundled("lib-trw.sh")
    assert not [w for w in result.get("warnings", []) if "lib-trw.sh" in w]
    assert not (initialized_repo / ".trw" / "trash").exists() or not any(
        p.read_text(encoding="utf-8", errors="replace") == _OLD_LIB
        for p in (initialized_repo / ".trw" / "trash").rglob("*")
        if p.is_file()
    )


_OLD_IG = '#!/bin/sh\n. "$(dirname "$0")/lib-trw.sh"\nold_ig() { :; }\n'
_OLD_IG_HOOK = (
    '#!/bin/sh\n. "$(dirname "$0")/lib-trw.sh"\n. "$(dirname "$0")/lib-intent-guard.sh"\nold_helper\nold_ig\n'
)


def _two_lib_family(repo: Path) -> tuple[dict[str, bytes], dict[str, str]]:
    """Both shared libs edited; pre-tool-intent-guard.sh (TRW's recorded old copy) sources both of them."""
    hooks = repo / ".claude" / "hooks"
    (hooks / "lib-trw.sh").write_text(_OLD_LIB + "# my tweak\n", encoding="utf-8")
    (hooks / "lib-intent-guard.sh").write_text(_OLD_IG + "# my tweak\n", encoding="utf-8")
    (hooks / "pre-tool-intent-guard.sh").write_text(_OLD_IG_HOOK, encoding="utf-8")
    manifest = {
        "lib-trw.sh": _sha(_OLD_LIB),
        "lib-intent-guard.sh": _sha(_OLD_IG),
        "pre-tool-intent-guard.sh": _sha(_OLD_IG_HOOK),
    }
    before = {n: (hooks / n).read_bytes() for n in ("lib-trw.sh", "lib-intent-guard.sh", "pre-tool-intent-guard.sh")}
    return before, manifest


@pytest.mark.parametrize("refused", ["lib-intent-guard.sh", "lib-trw.sh"])
def test_one_refused_backup_holds_every_lib_a_held_hook_sources(
    initialized_repo: Path, monkeypatch: pytest.MonkeyPatch, refused: str
) -> None:
    """Codex KI1: one lib refused and the other refreshed left a hook that sources both on a mixed family."""
    from trw_mcp.bootstrap import _hook_family
    from trw_mcp.bootstrap._trash import Removal

    real = _hook_family.remove_if_hash

    def refuse_one(path: Path, root: Path, sha: str, *, key: str | None = None) -> Removal:
        if path.name == refused:
            return Removal(key, path, "kept", path, None, "capture refused for the test")
        return real(path, root, sha, key=key)

    monkeypatch.setattr(_hook_family, "remove_if_hash", refuse_one)
    before, manifest = _two_lib_family(initialized_repo)

    result = _update(initialized_repo, manifest)

    hooks = initialized_repo / ".claude" / "hooks"
    after = {n: (hooks / n).read_bytes() if (hooks / n).is_file() else None for n in before}
    assert after == before, "a held hook's whole family (both libs it sources) stays exactly as it was"
    assert not [t for t in result.get("retired", []) if t.endswith(tuple(before))]
    assert "capture refused for the test" in " ".join(result.get("warnings", []))


def _refuse_trw_after_capturing_ig(monkeypatch: pytest.MonkeyPatch, fake_ig: object = None) -> None:
    from trw_mcp.bootstrap import _hook_family
    from trw_mcp.bootstrap._trash import Removal

    real = _hook_family.remove_if_hash

    def refuse_trw(path: Path, root: Path, sha: str, *, key: str | None = None) -> Removal:
        if path.name == "lib-trw.sh":
            return Removal(key, path, "kept", path, None, "capture refused for the test")
        if fake_ig is not None:
            return fake_ig(path, root, sha, key=key)  # type: ignore[operator]
        return real(path, root, sha, key=key)

    monkeypatch.setattr(_hook_family, "remove_if_hash", refuse_trw)


def test_a_writer_racing_the_link_back_keeps_both_copies_and_never_errors(
    initialized_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex KI1-r1 P0: a FileExistsError at link-back went to result.errors, whose rollback deleted the new file."""
    import os as real_os

    genuine_link = real_os.link  # read now: real_os IS os, so real_os.link later is the patched one

    from trw_mcp.bootstrap import _hook_family

    _refuse_trw_after_capturing_ig(monkeypatch)
    before, manifest = _two_lib_family(initialized_repo)
    racer = b"#!/bin/sh\n# written by a concurrent writer\n"

    def racing_link(src: object, dst: object, *args: object, **kwargs: object) -> None:
        Path(str(dst)).write_bytes(racer)
        raise FileExistsError(17, "File exists", str(dst))

    monkeypatch.setattr(_hook_family.os, "link", racing_link)
    try:
        result = _update(initialized_repo, manifest)
    finally:
        monkeypatch.setattr(_hook_family.os, "link", genuine_link)

    lib = initialized_repo / ".claude" / "hooks" / "lib-intent-guard.sh"
    assert lib.read_bytes() == racer, "the concurrent writer's bytes are never deleted"
    trash = initialized_repo / ".trw" / "trash"
    assert [p for p in trash.rglob("*") if p.is_file() and p.read_bytes() == before["lib-intent-guard.sh"]]
    assert not [e for e in result.get("errors", []) if "lib-intent-guard" in e], "an error triggers the rollback"
    notes = " ".join(result.get("warnings", []))
    assert "lib-intent-guard.sh" in notes and ".trw/trash" in notes and "concurrent" in notes


def test_an_unknown_capture_location_never_prints_none(initialized_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex KI1-r1 P2: retained_at=None printed 'backed up to None' as recovery advice."""
    from trw_mcp.bootstrap._trash import Removal

    def lost_track(path: Path, root: Path, sha: str, *, key: str | None = None) -> Removal:
        path.unlink()  # moved somewhere the outcome does not name
        return Removal(key, path, "removed", None, None, "")

    _refuse_trw_after_capturing_ig(monkeypatch, lost_track)
    _before, manifest = _two_lib_family(initialized_repo)

    result = _update(initialized_repo, manifest)

    text = " ".join(result.get("warnings", []) + result.get("errors", []))
    assert "None" not in text
    assert "lib-intent-guard.sh" in text and "unknown" in text


_MY_HOOK = '#!/bin/sh\n. "$(dirname "$0")/lib-trw.sh"\nold_helper\n# my local tweak\n'


def _edited_hook_family(repo: Path, lib_text: str) -> tuple[Path, dict[str, str]]:
    """session-start.sh edited by the user; it calls old_helper, which the bundled lib-trw.sh does not define."""
    hooks = repo / ".claude" / "hooks"
    (hooks / "lib-trw.sh").write_text(lib_text, encoding="utf-8")
    hook = hooks / "session-start.sh"
    hook.write_text(_MY_HOOK, encoding="utf-8")
    return hook, {"lib-trw.sh": _sha(_OLD_LIB), "session-start.sh": _sha(_OLD_HOOK)}


@pytest.mark.parametrize("lib_edited", [False, True])
def test_an_edited_hook_calling_a_dropped_function_is_backed_up_and_refreshed(
    initialized_repo: Path, lib_edited: bool
) -> None:
    """Codex KI2: a kept edited hook against a refreshed lib called functions that no longer exist."""
    hook, manifest = _edited_hook_family(initialized_repo, _OLD_LIB + ("# tweak\n" if lib_edited else ""))

    result = _update(initialized_repo, manifest)

    assert hook.read_text(encoding="utf-8") == _bundled("session-start.sh")
    trash = initialized_repo / ".trw" / "trash"
    assert [p for p in trash.rglob("*") if p.is_file() and p.read_text(errors="replace") == _MY_HOOK]
    notes = " ".join(result.get("warnings", []))
    assert "session-start.sh" in notes and "old_helper" in notes
    assert ".claude/hooks/session-start.sh" in result.get("retired", [])

    from trw_mcp.server._doctor_hook_family import hook_family_row

    assert hook_family_row(initialized_repo)[0] == "PASS"


def test_an_edited_hook_whose_calls_all_resolve_is_kept(initialized_repo: Path) -> None:
    hooks = initialized_repo / ".claude" / "hooks"
    (hooks / "lib-trw.sh").write_text(_OLD_LIB, encoding="utf-8")
    mine = '#!/bin/sh\n. "$(dirname "$0")/lib-trw.sh"\necho "my own hook"\n'
    (hooks / "session-start.sh").write_text(mine, encoding="utf-8")

    _update(initialized_repo, {"lib-trw.sh": _sha(_OLD_LIB), "session-start.sh": _sha(_OLD_HOOK)})

    assert (hooks / "session-start.sh").read_text(encoding="utf-8") == mine


def test_an_edited_hook_on_a_held_lib_is_left_alone(initialized_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.bootstrap import _hook_family
    from trw_mcp.bootstrap._trash import Removal

    monkeypatch.setattr(
        _hook_family,
        "remove_if_hash",
        lambda path, root, sha, *, key=None: Removal(key, path, "kept", path, None, "refused for the test"),
    )
    hook, manifest = _edited_hook_family(initialized_repo, _OLD_LIB + "# tweak\n")

    _update(initialized_repo, manifest)

    assert hook.read_text(encoding="utf-8") == _MY_HOOK, "its lib stays old, so its calls still resolve"


def test_an_edited_file_already_described_is_not_also_called_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """E2E-INC-133: the edited lib-trw.sh was reported as moved AND as an '(unchanged TRW file; see doctor)'."""
    import argparse

    from trw_mcp import bootstrap
    from trw_mcp.server import _subcommands

    edited = ".claude/hooks/lib-trw.sh"

    def fake_update(*_a: object, **_k: object) -> dict[str, list[str]]:
        return {
            "updated": [],
            "created": [],
            "preserved": [],
            "errors": [],
            "warnings": [f"{edited}: your edited copy was moved to .trw/trash/x/data and the bundled lib installed"],
            "retired": [edited, ".claude/hooks/old.sh"],
            "retired_described": [edited],
        }

    monkeypatch.setattr(bootstrap, "update_project", fake_update)
    args = argparse.Namespace(target_dir=str(tmp_path), pip_install=False, dry_run=False, ide=None)
    with pytest.raises(SystemExit):
        _subcommands._run_update_project(args)
    out = capsys.readouterr().out
    assert f"{edited} (unchanged TRW file" not in out, "an edited file must never be called unchanged"
    assert out.count(edited) == 1
    assert "Removed retired TRW file: .claude/hooks/old.sh" in out


@pytest.mark.skipif(not hasattr(__import__("os"), "mkfifo"), reason="no FIFOs on this platform")
def test_a_fifo_at_the_link_back_destination_is_never_read(tmp_path: Path) -> None:
    """Codex KI1-r2 KI2: dest.read_bytes() on a FIFO with an open writer blocks the update forever."""
    import os
    import threading

    from trw_mcp.bootstrap._hook_family import _link_back

    captured = tmp_path / "data"
    captured.write_bytes(b"#!/bin/sh\n# mine\n")
    dest = tmp_path / "lib-intent-guard.sh"
    os.mkfifo(dest)
    box: list[str | None] = []
    worker = threading.Thread(target=lambda: box.append(_link_back(captured, dest)), daemon=True)
    worker.start()
    worker.join(5)
    if worker.is_alive():  # unblock the reader so the test process can exit
        fd = os.open(dest, os.O_WRONLY | os.O_NONBLOCK)
        os.close(fd)
        worker.join(5)
        pytest.fail("_link_back blocked reading a FIFO at the destination")
    assert box and box[0] and str(captured) in box[0] and "not a regular file" in box[0]


def test_a_read_failure_after_a_collision_never_says_it_was_put_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex KI1-r2 KI3: FileExistsError then an unreadable dest claimed verification failed 'after being put back'."""
    from trw_mcp.bootstrap import _hook_family

    captured = tmp_path / "data"
    captured.write_bytes(b"mine\n")
    dest = tmp_path / "lib-intent-guard.sh"
    dest.write_bytes(b"theirs\n")
    real = Path.read_bytes

    def unreadable(self: Path) -> bytes:
        if self == dest:
            raise PermissionError(13, "Permission denied", str(dest))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    note = _hook_family._link_back(captured, dest)

    assert note and "put back" not in note and str(captured) in note


@pytest.mark.parametrize(
    "body",
    [
        'echo "old_helper is gone"\n',  # a name inside a double-quoted string is not a call
        "if true; then old_helper() { :; }; fi\nold_helper\n",  # defined by the hook itself, not at top level
    ],
)
def test_an_edited_hook_that_does_not_really_need_a_dropped_function_is_kept(initialized_repo: Path, body: str) -> None:
    """Codex KI2-r1 KI1: static parsing retired working edited hooks (a name in a string, a nested definition)."""
    hooks = initialized_repo / ".claude" / "hooks"
    (hooks / "lib-trw.sh").write_text(_OLD_LIB, encoding="utf-8")
    mine = '#!/bin/sh\n. "$(dirname "$0")/lib-trw.sh"\n' + body
    (hooks / "session-start.sh").write_text(mine, encoding="utf-8")

    _update(initialized_repo, {"lib-trw.sh": _sha(_OLD_LIB), "session-start.sh": _sha(_OLD_HOOK)})

    assert (hooks / "session-start.sh").read_text(encoding="utf-8") == mine


def test_a_function_now_defined_by_another_sourced_lib_is_not_missing(initialized_repo: Path) -> None:
    """Codex KI2-r1 KI2: definitions must be the union over every lib the hook sources."""
    from trw_mcp.server._doctor_hook_family import defined_functions

    moved = sorted(defined_functions(_bundled("lib-intent-guard.sh")) - defined_functions(_bundled("lib-trw.sh")))[0]
    hooks = initialized_repo / ".claude" / "hooks"
    old_trw = _OLD_LIB + f"{moved}() {{ :; }}\n"  # an older lib-trw.sh still defined it
    (hooks / "lib-trw.sh").write_text(old_trw, encoding="utf-8")
    (hooks / "lib-intent-guard.sh").write_text(_OLD_IG, encoding="utf-8")
    mine = f'#!/bin/sh\n. "$(dirname "$0")/lib-trw.sh"\n. "$(dirname "$0")/lib-intent-guard.sh"\n{moved}\n'
    (hooks / "pre-tool-intent-guard.sh").write_text(mine, encoding="utf-8")
    manifest = {
        "lib-trw.sh": _sha(old_trw),
        "lib-intent-guard.sh": _sha(_OLD_IG),
        "pre-tool-intent-guard.sh": _sha(_OLD_IG_HOOK),
    }

    _update(initialized_repo, manifest)

    assert (hooks / "pre-tool-intent-guard.sh").read_text(encoding="utf-8") == mine


@pytest.mark.skipif(not hasattr(__import__("os"), "mkfifo"), reason="no FIFOs on this platform")
def test_a_fifo_lib_never_blocks_settling(initialized_repo: Path) -> None:
    """Codex KI2-r1 KI3: reading old lib definitions opened a writerless FIFO and stalled update-project."""
    import os
    import threading

    from trw_mcp.bootstrap._hook_family import settle_edited_libs
    from trw_mcp.bootstrap._utils import _DATA_DIR

    lib = initialized_repo / ".claude" / "hooks" / "lib-trw.sh"
    lib.unlink()
    os.mkfifo(lib)
    shipped = {p.name for p in (_DATA_DIR / "hooks").glob("*.sh")}
    done: list[bool] = []
    worker = threading.Thread(
        target=lambda: done.append(
            bool(settle_edited_libs(initialized_repo, _DATA_DIR / "hooks", shipped, {}, {})) or True
        ),
        daemon=True,
    )
    worker.start()
    worker.join(10)
    if worker.is_alive():
        fd = os.open(lib, os.O_WRONLY | os.O_NONBLOCK)
        os.close(fd)
        worker.join(5)
        pytest.fail("settle_edited_libs blocked reading a FIFO lib")
    assert done


def test_a_retained_capture_never_claims_the_bundled_hook_was_installed(
    initialized_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex KI2-r1 KI4: retained means someone else's file now holds the name; the update keeps it."""
    from trw_mcp.bootstrap import _hook_family
    from trw_mcp.bootstrap._trash import Removal

    hook, manifest = _edited_hook_family(initialized_repo, _OLD_LIB)
    at = initialized_repo / ".trw" / "trash" / "x" / "data"

    def retained(path: Path, root: Path, sha: str, *, key: str | None = None) -> Removal:
        at.parent.mkdir(parents=True, exist_ok=True)
        at.write_bytes(path.read_bytes())
        path.write_text("#!/bin/sh\n# a concurrent writer\n", encoding="utf-8")
        return Removal(key, path, "retained", None, at, "bytes changed during capture")

    monkeypatch.setattr(_hook_family, "remove_if_hash", retained)

    result = _update(initialized_repo, manifest)

    notes = " ".join(w for w in result.get("warnings", []) if "session-start.sh" in w)
    assert "bundled hook installed" not in notes and str(at) in notes
    assert at.read_text(encoding="utf-8") == _MY_HOOK, "the user's edited hook survives in trash"
    assert hook.read_text(encoding="utf-8") == "#!/bin/sh\n# a concurrent writer\n", "the concurrent file is kept"


def test_an_edited_hook_the_checker_cannot_verify_is_kept_with_a_warning(initialized_repo: Path) -> None:
    """Lead ruling on KI2-r3: a user's edited hook is never retired on a guess."""
    deep = 'x="' + '$(echo "' * 600 + "y" + '")' * 600 + '"\n'
    hook, manifest = _edited_hook_family(initialized_repo, _OLD_LIB)
    mine = _MY_HOOK + deep
    hook.write_text(mine, encoding="utf-8")

    result = _update(initialized_repo, manifest)

    assert hook.read_text(encoding="utf-8") == mine
    notes = " ".join(w for w in result.get("warnings", []) if "session-start.sh" in w)
    assert "kept" in notes and "could not be verified" in notes
    assert ".claude/hooks/session-start.sh" not in result.get("retired", [])


def _real_git(repo: Path) -> None:
    """Swap the fixture's fake ``.git`` for a real repo with everything committed."""
    import shutil
    import subprocess

    shutil.rmtree(repo / ".git")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_a_git_clean_edited_lib_is_replaced_in_place_without_trash(initialized_repo: Path) -> None:
    lib, hook, manifest = _old_family(initialized_repo)
    _real_git(initialized_repo)

    result = _update(initialized_repo, manifest)

    assert lib.read_text(encoding="utf-8") == _bundled("lib-trw.sh")
    assert hook.read_text(encoding="utf-8") == _bundled("session-start.sh")
    assert not (initialized_repo / ".trw" / "trash").exists()
    rel = ".claude/hooks/lib-trw.sh"
    assert (
        f"{rel}: removed; your version differs from TRW's but is committed in git (restore: git restore -- {rel})"
        in result["warnings"]
    )
    assert rel in result["retired"]


def test_a_git_clean_edited_hook_calling_a_dropped_function_is_replaced_in_place(initialized_repo: Path) -> None:
    hook, manifest = _edited_hook_family(initialized_repo, _OLD_LIB)
    _real_git(initialized_repo)

    result = _update(initialized_repo, manifest)

    assert hook.read_text(encoding="utf-8") == _bundled("session-start.sh")
    assert not (initialized_repo / ".trw" / "trash").exists()
    rel = ".claude/hooks/session-start.sh"
    assert (
        f"{rel}: removed; your version differs from TRW's but is committed in git (restore: git restore -- {rel})"
        in result["warnings"]
    )


def test_the_dirty_file_restore_never_deletes_a_writer_that_raced_the_link_back(
    initialized_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """FB-01-KI1-RACE (codex KI1-r2, HB-2): after the link-back kept a concurrent writer's file, the uncommitted-
    changes restore put the pre-run snapshot back over it, unlinking the writer's bytes. The user's edit is
    already in .trw/trash, so the restore must leave that path alone."""
    import os as real_os

    genuine_link = real_os.link  # read now: real_os IS os, so real_os.link later is the patched one
    import shutil

    from trw_mcp.bootstrap import _hook_family
    from trw_mcp.bootstrap._version_manifest import preserve_uncommitted_changes

    _refuse_trw_after_capturing_ig(monkeypatch)
    before, manifest = _two_lib_family(initialized_repo)
    snapshot = tmp_path_factory.mktemp("snapshot")
    rel = ".claude/hooks/lib-intent-guard.sh"
    (snapshot / rel).parent.mkdir(parents=True)
    shutil.copy2(initialized_repo / rel, snapshot / rel)  # the pre-run tree holds the user's (uncommitted) edit
    racer = b"#!/bin/sh\n# written by a concurrent writer\n"

    def racing_link(src: object, dst: object, *args: object, **kwargs: object) -> None:
        Path(str(dst)).write_bytes(racer)
        raise FileExistsError(17, "File exists", str(dst))

    monkeypatch.setattr(_hook_family.os, "link", racing_link)
    try:
        result = _update(initialized_repo, manifest)
    finally:
        monkeypatch.setattr(_hook_family.os, "link", genuine_link)
    assert (initialized_repo / rel).read_bytes() == racer

    preserve_uncommitted_changes(initialized_repo, snapshot, {rel}, manifest, result)

    assert (initialized_repo / rel).read_bytes() == racer, "the writer's bytes were deleted by the restore"
    trash = initialized_repo / ".trw" / "trash"
    assert [p for p in trash.rglob("*") if p.is_file() and p.read_bytes() == before["lib-intent-guard.sh"]]


def test_a_vanished_capture_never_costs_the_users_edit_or_the_writers_file(
    initialized_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Codex FB01-KI1-RACE r1 P0: the path was marked trashed without proof its capture still existed, so the
    restore skipped it and snapshot cleanup deleted the last copy of the user's edit. Unproven capture: the
    writer's file is captured instead and the restore brings the user's edit back. Both survive."""
    import os as real_os

    genuine_link = real_os.link  # read now: real_os IS os, so real_os.link later is the patched one
    import shutil

    from trw_mcp.bootstrap import _hook_family
    from trw_mcp.bootstrap._version_manifest import preserve_uncommitted_changes

    _refuse_trw_after_capturing_ig(monkeypatch)
    before, manifest = _two_lib_family(initialized_repo)
    rel = ".claude/hooks/lib-intent-guard.sh"
    snapshot = tmp_path_factory.mktemp("snapshot")
    (snapshot / rel).parent.mkdir(parents=True)
    shutil.copy2(initialized_repo / rel, snapshot / rel)
    racer = b"#!/bin/sh\n# written by a concurrent writer\n"

    def racing_link(src: object, dst: object, *args: object, **kwargs: object) -> None:
        Path(str(src)).unlink()  # the capture vanished before the link-back
        Path(str(dst)).write_bytes(racer)
        raise FileExistsError(17, "File exists", str(dst))

    monkeypatch.setattr(_hook_family.os, "link", racing_link)
    try:
        result = _update(initialized_repo, manifest)
    finally:
        monkeypatch.setattr(_hook_family.os, "link", genuine_link)

    assert rel not in result.get("retired", []), "an unproven capture must never exempt the path from the restore"
    preserve_uncommitted_changes(initialized_repo, snapshot, {rel}, manifest, result)

    assert (initialized_repo / rel).read_bytes() == before["lib-intent-guard.sh"], "the user's edit is restored"
    trash = initialized_repo / ".trw" / "trash"
    assert [p for p in trash.rglob("*") if p.is_file() and p.read_bytes() == racer], "the writer's file survives"


def test_an_unusable_trash_never_costs_the_writers_only_copy(
    initialized_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Partial codex r2 lead (content-stopped review, verified here): ONE shared fault, an unusable .trw/trash, fails
    both the capture proof and the writer's capture, and the restore then deleted the writer's only copy. The
    writer's file is set aside beside the name instead, which needs no trash."""
    import os as real_os

    genuine_link = real_os.link  # read now: real_os IS os, so real_os.link later is the patched one
    import shutil

    from trw_mcp.bootstrap import _hook_family
    from trw_mcp.bootstrap._trash import Removal
    from trw_mcp.bootstrap._version_manifest import preserve_uncommitted_changes

    real_remove = _hook_family.remove_if_hash
    state = {"captured": False}

    def trash_breaks_after_first_capture(path: Path, root: Path, sha: str, *, key: str | None = None) -> Removal:
        if path.name == "lib-trw.sh":
            return Removal(key, path, "kept", path, None, "capture refused for the test")
        if state["captured"]:
            return Removal(key, path, "kept", path, None, "trash not writable")
        state["captured"] = True
        return real_remove(path, root, sha, key=key)

    monkeypatch.setattr(_hook_family, "remove_if_hash", trash_breaks_after_first_capture)
    before, manifest = _two_lib_family(initialized_repo)
    rel = ".claude/hooks/lib-intent-guard.sh"
    snapshot = tmp_path_factory.mktemp("snapshot")
    (snapshot / rel).parent.mkdir(parents=True)
    shutil.copy2(initialized_repo / rel, snapshot / rel)
    racer = b"#!/bin/sh\n# written by a concurrent writer\n"

    def racing_link(src: object, dst: object, *args: object, **kwargs: object) -> None:
        Path(str(src)).unlink()  # the trash became unusable: the capture cannot be proven
        Path(str(dst)).write_bytes(racer)
        raise FileExistsError(17, "File exists", str(dst))

    monkeypatch.setattr(_hook_family.os, "link", racing_link)
    try:
        result = _update(initialized_repo, manifest)
    finally:
        monkeypatch.setattr(_hook_family.os, "link", genuine_link)
    preserve_uncommitted_changes(initialized_repo, snapshot, {rel}, manifest, result)

    assert (initialized_repo / rel).read_bytes() == before["lib-intent-guard.sh"], "the user's edit is restored"
    # r4: the restore moved the writer's bytes into .trw/trash before putting the edit back (positive proof).
    survivors = [p for p in initialized_repo.rglob("*") if p.is_file() and p.read_bytes() == racer]
    assert survivors, "the writer's only copy was deleted"
    assert any(rel in w and ".trw/trash" in w for w in result.get("warnings", [])), "the warning names where it went"


@pytest.mark.skipif(
    not hasattr(__import__("os"), "geteuid") or __import__("os").geteuid() == 0, reason="root ignores modes"
)
def test_a_mode_000_trash_never_costs_either_copy(
    initialized_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Lead's case: a real trash directory made inaccessible (mode 000) mid-update, plus a concurrent writer."""
    import os as real_os

    genuine_link = real_os.link  # read now: real_os IS os, so real_os.link later is the patched one
    import shutil

    from trw_mcp.bootstrap import _hook_family
    from trw_mcp.bootstrap._version_manifest import preserve_uncommitted_changes

    _refuse_trw_after_capturing_ig(monkeypatch)
    before, manifest = _two_lib_family(initialized_repo)
    rel = ".claude/hooks/lib-intent-guard.sh"
    snapshot = tmp_path_factory.mktemp("snapshot")
    (snapshot / rel).parent.mkdir(parents=True)
    shutil.copy2(initialized_repo / rel, snapshot / rel)
    racer = b"#!/bin/sh\n# written by a concurrent writer\n"
    trash = initialized_repo / ".trw" / "trash"

    def racing_link(src: object, dst: object, *args: object, **kwargs: object) -> None:
        Path(str(dst)).write_bytes(racer)
        trash.chmod(0)
        raise FileExistsError(17, "File exists", str(dst))

    monkeypatch.setattr(_hook_family.os, "link", racing_link)
    try:
        result = _update(initialized_repo, manifest)
        # r5: with the trash unusable the restore can neither prove nor capture the writer's bytes, so it KEEPS
        # them untouched and names the path (fail closed, without aborting anything else).
        preserve_uncommitted_changes(initialized_repo, snapshot, {rel}, manifest, result)
        assert any(rel in w and "left in place" in w for w in result.get("warnings", []))
    finally:
        monkeypatch.setattr(_hook_family.os, "link", genuine_link)
        trash.chmod(0o755)

    hooks = initialized_repo / ".claude" / "hooks"
    assert [p for p in hooks.iterdir() if p.is_file() and p.read_bytes() == racer], "the writer's copy survives"
    edit = before["lib-intent-guard.sh"]
    copies = [f for d in (trash, snapshot) for f in d.rglob("*") if f.is_file() and f.read_bytes() == edit]
    assert copies, "the user's edit survives (trash capture or the kept snapshot)"
