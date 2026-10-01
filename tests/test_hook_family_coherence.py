"""FB-INSTALL-01: update-project keeps the hook family coherent when a shared lib was edited.

It used to keep an edited ``lib-trw.sh`` (an older 588-line copy) while replacing every hook that sources
it, so ~20 functions were undefined and each hook silently exited 0. Now the edited lib is backed up to
``.trw/trash`` (named in the report) and refreshed together with its dependents; if it cannot be backed up,
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
    assert ".claude/hooks/lib-trw.sh" in result.get("trashed", []), "or the dirty-file restore puts the old lib back"

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
    assert not [t for t in result.get("trashed", []) if t.endswith(tuple(before))]
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
        monkeypatch.setattr(_hook_family.os, "link", real_os.link)

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
