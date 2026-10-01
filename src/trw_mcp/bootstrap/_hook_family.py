"""Keep the deployed hook family coherent across an update (FB-INSTALL-01).

Belongs to ``_template_updater._update_hooks``.

Every hook used to be decided on its own: an edited ``lib-trw.sh`` was kept while the hooks that source it
were replaced, so the new hooks called ~20 functions the old lib lacked and each one silently exited 0.
A shared ``lib-*.sh`` and its dependents now move together. An edited lib is captured into ``.trw/trash``
(the path is named in the report) and then refreshed WITH its dependents: the user's bytes survive, the
family is current, and a stale lib no longer pins old behaviour such as ignoring ``hooks_enabled``. When the
capture is refused, the lib and every hook that sources it are left exactly as they are, so the old
family stays whole. A held hook also holds every OTHER lib it sources (codex KI1: a hook sourcing two libs
must not run on one old and one new), so every decision is made before anything moves, and a lib already
captured when a later one is refused is linked back from trash (the trash copy stays).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

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
    try:
        os.link(captured_at, dest)
    except FileExistsError:
        pass  # trw-fail-silent-allow: a concurrent writer got there first; its bytes are compared below
    except OSError as exc:
        return f"could not be put back ({exc}); your edit is at {captured_at}"
    try:
        if dest.read_bytes() == captured_at.read_bytes():
            return None
    except OSError as exc:
        return f"could not be checked after being put back ({exc}); your edit is at {captured_at}"
    return f"was replaced by a concurrent writer while being put back; your edit is at {captured_at}"


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
    for lib, current in edited.items():
        if lib in held:
            continue
        # Captured by rename and re-verified, never unlinked: a racing edit keeps its bytes (HB-2).
        outcome = remove_if_hash(hooks / lib, target_dir, current, key=f".claude/hooks/{lib}")
        if outcome.status in ("removed", "retained", "absent"):
            captured[lib] = outcome.retained_at
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
            continue
        result.setdefault("trashed", []).append(rel)  # or the dirty-file restore puts the old lib back
        warnings.append(
            f"{rel}: your edited copy was moved to {at or '.trw/trash'} and the bundled lib installed, because"
            f" the {len(deps[lib])} hooks that source it were updated and need its functions"
        )
    for lib, reason in sorted(refused.items()):
        warnings.append(
            f".claude/hooks/{lib}: edited, and could not be backed up ({reason}); it and the hooks that source it"
            f" ({', '.join(sorted(deps[lib]))}) were left unchanged so they stay consistent"
        )
    others = sorted(n for n in held if n.startswith("lib-") and n not in refused and n not in unrestored)
    if others:
        warnings.append(f"{', '.join(others)}: left unchanged too, because hooks held above also source them")
    return held
