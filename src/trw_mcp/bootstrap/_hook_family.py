"""Keep the deployed hook family coherent across an update (FB-INSTALL-01).

Belongs to ``_template_updater._update_hooks``.

Every hook used to be decided on its own: an edited ``lib-trw.sh`` was kept while the hooks that source it
were replaced, so the new hooks called ~20 functions the old lib lacked and each one silently exited 0.
A shared ``lib-*.sh`` and its dependents now move together. An edited lib that git holds clean is replaced in
place (the report names the restore command); one with uncommitted edits is captured into ``.trw/trash``
(the path is named in the report) and then refreshed WITH its dependents: the user's bytes survive, the
family is current, and a stale lib no longer pins old behaviour such as ignoring ``hooks_enabled``. When the
capture is refused, the lib and every hook that sources it are left exactly as they are, so the old
family stays whole. A held hook also holds every OTHER lib it sources (codex KI1: a hook sourcing two libs
must not run on one old and one new), so every decision is made before anything moves, and a lib already
captured when a later one is refused is linked back from trash (the trash copy stays). An EDITED hook that
calls a function its refreshed lib no longer defines is backed up and refreshed the same way (codex KI2);
an edited hook whose calls all still resolve is kept.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from trw_mcp.server._doctor_hook_family import defined_functions, sourced_libs, verify_calls

from ._retire import as_retirement, describe_removal, record_retirement, retire_file
from ._safe_remove import remove_if_hash
from ._version_manifest import _framework_content_hashes, _is_user_modified

__all__ = ["settle_edited_libs"]


def _dependents(lib: str, shipped: set[str], hooks_source: Path) -> set[str]:
    out = set()
    for name in shipped:
        if name == lib:
            continue
        try:
            if lib in (hooks_source / name).read_text(encoding="utf-8", errors="replace"):
                out.add(name)
        except OSError:
            out.add(name)  # unreadable bundle file: assume it depends, so it is held rather than half-updated
    return out


def _text(path: Path) -> str:
    try:
        # lstat first: a FIFO or device would block or misbehave on read (codex KI2-r1 KI3).
        if not stat.S_ISREG(path.lstat().st_mode):
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # trw-fail-silent-allow: a missing or unreadable file defines and calls nothing
        return ""


def _closure(held: set[str], libs: list[str], deps: dict[str, set[str]]) -> set[str]:
    """A held lib holds its dependents; a held hook holds every lib it sources. Grow until stable."""
    held = set(held)
    while True:
        grown = set(held)
        for lib in libs:
            if lib in grown or deps[lib] & grown:
                grown |= deps[lib] | {lib}
        if grown == held:
            return held
        held = grown


def _link_back(captured_at: Path | None, dest: Path) -> str | None:
    """Put a captured lib back at its name (a hard link: the trash copy stays).

    Returns None only when *dest* now holds the captured bytes, else what the user must know. Never an
    error: an error starts the update rollback, which would delete whatever a concurrent writer put at
    *dest* (codex KI1-r1). An occupied *dest* is left as it is.
    """
    if captured_at is None:
        return "was backed up, but its capture location is unknown (look in .trw/trash)"
    linked = False
    try:
        os.link(captured_at, dest)
        linked = True
    except FileExistsError:
        pass  # trw-fail-silent-allow: a concurrent writer got there first; its bytes are compared below
    except OSError as exc:
        return f"could not be put back ({exc}); your edit is at {captured_at}"
    try:
        # lstat first: a FIFO or device at dest would block or misbehave on read (codex KI1-r2 KI2).
        if not stat.S_ISREG(dest.lstat().st_mode):
            return f"is now occupied by something that is not a regular file; your edit is at {captured_at}"
        if dest.read_bytes() == captured_at.read_bytes():
            return None
    except OSError as exc:
        if linked:
            return f"could not be checked after being put back ({exc}); your edit is at {captured_at}"
        return f"is occupied by a file that could not be read ({exc}); your edit is at {captured_at}"
    return f"was replaced by a concurrent writer while being put back; your edit is at {captured_at}"


def _occupied(path: Path) -> bool:
    """True unless the name is provably free: a permission error is NOT "absent" (codex review R4)."""
    try:
        os.lstat(path)
    except FileNotFoundError:  # trw-fail-silent-allow: not silent, absent is the answer this probe asks for
        return False
    except OSError:  # trw-fail-silent-allow: unknown means occupied, the safe direction
        return True
    return True


def _settle_occupied_name(
    target_dir: Path, rel: str, captured_at: Path | None, user_sha: str, result: dict[str, list[str]]
) -> None:
    """A concurrent writer holds *rel* after the link-back: its file stays at the name, never moved (HB-2).

    When the capture provably still holds the user's edit, *rel* is marked retired so the dirty-file restore
    leaves the writer's file alone (proof re-taken now, as remove_proven does at the act). Otherwise nothing
    is marked: if the restore later puts the user's edit back, it first moves the writer's bytes into
    .trw/trash, because a restore deletes only bytes this run wrote (_restore_proof, FB-01-KI1-RACE r4). The
    name is never left empty, so the hooks that source the lib keep a lib (codex review F).
    """
    try:
        proven = captured_at is not None and hashlib.sha256(captured_at.read_bytes()).hexdigest() == user_sha
    except OSError:  # trw-fail-silent-allow: an unreadable capture is no proof; the restore's own proof then guards
        proven = False
    if proven:
        result.setdefault("retired", []).append(rel)
        return
    result.setdefault("warnings", []).append(
        f"{rel}: a concurrent writer's file was left here; your edit's backup in .trw/trash could not be"
        " confirmed, so check that folder for it"
    )


def settle_edited_libs(
    target_dir: Path,
    hooks_source: Path,
    shipped: set[str],
    manifest_hashes: dict[str, str] | None,
    result: dict[str, list[str]],
) -> set[str]:
    """Back up each edited shared lib so it refreshes with its hooks; return the names to leave untouched."""
    hooks = target_dir / ".claude" / "hooks"
    libs = sorted(n for n in shipped if n.startswith("lib-"))
    deps = {lib: _dependents(lib, shipped, hooks_source) for lib in libs}
    old_defs = {lib: defined_functions(_text(hooks / lib)) for lib in libs}
    edited: dict[str, str] = {}
    refused: dict[str, str] = {}
    for lib in libs:
        dest = hooks / lib
        if not dest.is_file() or not _is_user_modified(
            dest, lib, manifest_hashes, framework_hashes=_framework_content_hashes(hooks_source / lib)
        ):
            continue
        if dest.is_symlink():
            refused[lib] = "not a regular file"
            continue
        try:
            edited[lib] = hashlib.sha256(dest.read_bytes()).hexdigest()
        except OSError as exc:
            refused[lib] = f"could not read it: {exc}"
    # Decide before moving anything, so a refusal can never strand a family half-replaced.
    held = _closure(set(refused), libs, deps)
    captured: dict[str, Path | None] = {}
    retained: set[str] = set()  # a file written at the name during capture holds it now
    for lib, current in edited.items():
        if lib in held:
            continue
        # Committed and clean in git: git holds the edit, so it is replaced in place. Only uncommitted bytes need
        # the capture below (by rename and re-verified, never unlinked: a racing edit keeps its bytes, HB-2).
        gone = retire_file(hooks / lib, target_dir, (), managed=True)
        if gone.status == "git":
            record_retirement(result, as_retirement(f".claude/hooks/{lib}", gone))
            continue
        outcome = remove_if_hash(hooks / lib, target_dir, current, key=f".claude/hooks/{lib}")
        if outcome.status in ("removed", "retained", "absent"):
            captured[lib] = outcome.retained_at
            if outcome.status == "retained":
                retained.add(lib)
            continue
        refused[lib] = outcome.reason
        held = _closure(held | {lib}, libs, deps)
    warnings = result.setdefault("warnings", [])
    unrestored: set[str] = set()
    for lib, at in captured.items():
        rel = f".claude/hooks/{lib}"
        if lib in held:
            problem = _link_back(at, hooks / lib)
            if problem:
                unrestored.add(lib)
                warnings.append(f"{rel}: {problem}")
                if _occupied(hooks / lib):
                    _settle_occupied_name(target_dir, rel, at, edited[lib], result)
            continue
        result.setdefault("retired", []).append(rel)  # or the dirty-file restore puts the old lib back
        now = "a file written there meanwhile was kept" if lib in retained else "the bundled lib installed"
        why = f"the {len(deps[lib])} hooks that source it were updated and need its functions"
        describe_removal(
            result,
            rel,
            f"{rel}: your edited copy was moved to {at or '.trw/trash'} and {now}, because {why}",
            f"{rel}: your edited copy would be moved to .trw/trash and {now}, because {why}",
        )
    for lib, reason in sorted(refused.items()):
        warnings.append(
            f".claude/hooks/{lib}: edited, and could not be backed up ({reason}); it and the hooks that source it"
            f" ({', '.join(sorted(deps[lib]))}) were left unchanged so they stay consistent"
        )
    others = sorted(n for n in held if n.startswith("lib-") and n not in refused and n not in unrestored)
    if others:
        warnings.append(f"{', '.join(others)}: left unchanged too, because hooks held above also source them")
    _refresh_stranded_hooks(target_dir, hooks_source, shipped, manifest_hashes, result, held, old_defs)
    return held


def _refresh_stranded_hooks(
    target_dir: Path,
    hooks_source: Path,
    shipped: set[str],
    manifest_hashes: dict[str, str] | None,
    result: dict[str, list[str]],
    held: set[str],
    old_defs: dict[str, set[str]],
) -> None:
    """Back up each edited hook that calls a function its refreshed lib drops, so the bundled hook installs."""
    hooks = target_dir / ".claude" / "hooks"
    new_defs = {lib: defined_functions(_text(hooks_source / lib)) for lib in old_defs}
    for name in sorted(n for n in shipped - held if not n.startswith("lib-")):
        dest = hooks / name
        if not dest.is_file() or dest.is_symlink():
            continue
        if not _is_user_modified(
            dest, name, manifest_hashes, framework_hashes=_framework_content_hashes(hooks_source / name)
        ):
            continue
        text = _text(dest)
        libs = sorted({lib for lib in sourced_libs(text) if lib in old_defs and lib not in held})
        lost, unsure = verify_calls(text, libs, have=new_defs, had=old_defs)
        rel = f".claude/hooks/{name}"
        if unsure:
            # Lead ruling (KI2-r3): never retire a user's edited hook on a guess. Keep it and say so.
            result.setdefault("warnings", []).append(
                f"{rel}: your edited hook was kept; its calls could not be verified against the updated"
                f" {', '.join(libs) or 'libs'} ({unsure}). Check it by hand: `trw-mcp doctor` shows hook_family."
            )
            continue
        if not lost:
            continue
        calls = ", ".join(sorted(lost))
        gone = retire_file(dest, target_dir, (), managed=True)
        if gone.status == "git":
            record_retirement(result, as_retirement(rel, gone))
            continue
        outcome = remove_if_hash(dest, target_dir, hashlib.sha256(dest.read_bytes()).hexdigest(), key=rel)
        if outcome.status in ("removed", "retained", "absent"):
            result.setdefault("retired", []).append(rel)
            at = outcome.retained_at or ".trw/trash"
            # retained: a file written at the name during capture holds it now, and the update keeps it (KI2-r1 KI4).
            now = (
                "a file written there meanwhile was kept"
                if outcome.status == "retained"
                else "the bundled hook installed"
            )
            gone_calls = f"your edited hook calls {calls}, which the updated {', '.join(libs)} no longer defines"
            describe_removal(
                result,
                rel,
                f"{rel}: {gone_calls}; it was moved to {at} and {now}"
                " (to restore it, copy it back and re-add the missing functions)",
                f"{rel}: {gone_calls}; it would be moved to .trw/trash and {now}",
            )
        else:
            result.setdefault("warnings", []).append(
                f"{rel}: your edited hook calls {calls}, which the updated {', '.join(libs)} no longer defines,"
                f" and it could not be backed up ({outcome.reason}); it was kept and will fail until edited"
            )
