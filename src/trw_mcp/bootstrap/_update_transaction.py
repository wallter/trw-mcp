"""Filesystem snapshot and rollback support for ``update_project``."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path

from trw_memory._tree_removal import remove_tree

from trw_mcp.canons.registry import install_view, load_registry
from trw_mcp.framework_deployment import DEPLOYMENT_RELATIVE_PATH
from trw_mcp.state.claude_md._instructions_link import INSTRUCTIONS_RELPATH

from ._managed_dirs import (
    _PRUNED_NESTED_DIR_NAMES as _PRUNED_NESTED_DIR_NAMES,
)
from ._managed_dirs import (
    _TRANSACTION_DIRS as _TRANSACTION_DIRS,
)
from ._managed_dirs import (
    _has_git_marker as _has_git_marker,
)
from ._managed_dirs import (
    _is_pruned_nested_dir as _is_pruned_nested_dir,
)
from ._managed_dirs import (
    _is_under_pruned_dir as _is_under_pruned_dir,
)
from ._managed_dirs import (
    _is_user_skill_link as _is_user_skill_link,
)
from ._managed_dirs import (
    _managed_kind as _managed_kind,
)
from ._refused_restore import new_snapshot_dir, release_snapshot
from ._retire import git_view_of
from ._utils import printable

_CANON_REGISTRY = load_registry()
#: Every canon install target (the .trw runtime copies and the root project references such as
#: FRAMEWORK.md and AARE-F-FRAMEWORK.md) plus the deployment record, read from the canon registry so a
#: new target is snapshotted and reported without a hand-kept entry here.
_MANAGED_CANON_FILES: tuple[str, ...] = tuple(
    dict.fromkeys(
        [
            *(destination for _, destination in install_view(_CANON_REGISTRY)),
            str(DEPLOYMENT_RELATIVE_PATH),
        ]
    )
)
_TRANSACTION_FILES: tuple[str, ...] = (
    *_MANAGED_CANON_FILES,
    # Snapshot only update-owned .trw artifacts. The .trw root also contains
    # live memory, learnings, runs, dispatch jobs, and runtime pins; restoring
    # that directory wholesale can erase writes made after the snapshot.
    ".trw/.gitignore",
    ".trw/channels/manifest.yaml",
    ".trw/client-profile.env",
    ".trw/config.yaml",
    # PRD-SEC-013 marker: update-project re-blesses its hook digest after a resync.
    ".trw/contracts/enrollment.yaml",
    ".trw/context/behavioral_protocol.md",
    ".trw/context/behavioral_protocol.yaml",
    ".trw/context/messages.yaml",
    ".trw/credentials.yaml",
    ".trw/installer-meta.yaml",
    ".trw/managed-artifacts.yaml",
    # PRD-FIX-118/R8 sol round 2 P2: still written (kept alive, refreshed on
    # every hook-env write) while a not-yet-upgraded installed lib-trw.sh
    # predates the per-client hook-env.d split -- see
    # bootstrap/_hook_env.py::_installed_lib_predates_hook_env_split /
    # _write_hook_env_file. A rollback that omits this path restores the old
    # (still pre-split) lib-trw.sh without restoring the env file it directly
    # sources, leaving the two silently mismatched. Keep this entry as long as
    # that migration-compat branch exists; drop both together once it retires.
    ".trw/runtime/hook-env.sh",
    ".trw/templates/claude_md.md",
    ".trw/frameworks/VERSION.yaml",
    # PRD-CORE-341: AGENTS.md imports it, so a rollback restores the pair together.
    INSTRUCTIONS_RELPATH,
    ".mcp.json",
    "AGENTS.md",
    "ANTIGRAVITY.md",
    # No root CLAUDE.md: update never writes one (REMOVE-S2), so a rollback must not restore it over a concurrent
    # edit either.
    "opencode.json",
    "REVIEW.md",
)


def _refuse_git_marked_owned_dirs(target_dir: Path) -> None:
    """Refuse to write into a TRW-owned dir that carries a ``.git`` marker.

    ``.claude/skills/trw-deliver/.git`` may be the user's own clone; writing SKILL.md into it would
    corrupt their repo, and pruning it would leave the writers writing unsnapshotted. Refusing names it.
    Deeper in the subtree only a clear submodule checkout is pruned; any other marker is refused.
    """
    for rel in _TRANSACTION_DIRS:
        top = target_dir / rel
        if top.is_symlink() or not top.is_dir():
            continue
        for dirpath, dirnames, _files in os.walk(top, followlinks=False):
            base = Path(dirpath)
            dirnames[:] = [d for d in dirnames if not _is_pruned_nested_dir(base / d, target_dir)]
            if _managed_kind(base, target_dir) == "owned" and _has_git_marker(base):
                raise OSError(
                    f"managed directory {base.relative_to(target_dir).as_posix()} contains a .git marker "
                    "(a nested repository?); refusing to update into it. Move it out of the managed "
                    "directory or remove the .git entry, then rerun"
                )


def _snapshot_copy_ignore(root: Path, *, denied_is_marker: bool = False) -> Callable[[str, list[str]], set[str]]:
    """``shutil.copytree`` ignore callback factory: skip nested runtime dirs (worktrees /
    nested git repos) so a snapshot never copies unmanaged, symlink-bearing state. *root* is the
    project (or snapshot) root the copied paths are relative to."""

    def ignore(directory: str, names: list[str]) -> set[str]:
        base = Path(directory)
        return {
            name
            for name in names
            if _is_special_file(base / name)
            or _is_pruned_nested_dir(base / name, root, denied_is_marker=denied_is_marker)
        }

    return ignore


def _is_special_file(path: Path) -> bool:
    """A FIFO, socket or device node (``lstat``). Never copied into a snapshot (a FIFO would abort the
    whole update) and never touched by a rollback, which removes only regular files, symlinks and dirs."""
    try:
        mode = path.lstat().st_mode
    except OSError:  # trw-fail-silent-allow: gone or unreadable; copytree then reports it on its own
        return False
    return not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode))


def _remove_transaction_path(path: Path, root: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        # Preserve nested runtime dirs (worktrees / nested git repos) so a
        # rollback that rewrites a managed dir never deletes unmanaged state.
        try:
            children = list(path.iterdir())
        except (
            PermissionError
        ):  # trw-fail-silent-allow: an unreadable dir is kept as is; a rollback never stops part-way
            return
        for child in children:
            # A pruned NAME (``.git`` file/link included) was never snapshotted, so unlinking it
            # here would lose it for good.
            if child.name in _PRUNED_NESTED_DIR_NAMES or (
                child.is_dir() and not child.is_symlink() and _is_pruned_nested_dir(child, root, denied_is_marker=True)
            ):
                continue
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                _remove_transaction_path(child, root)
        # Remove the now-managed-empty dir only if nothing survived (no pruned
        # children); otherwise leave it holding the preserved worktrees.
        if not any(path.iterdir()):
            path.rmdir()


def _reject_symlink_path(target_dir: Path, rel: str) -> None:
    """Refuse a managed leaf or ancestor that can redirect writes outside."""
    current = target_dir
    for part in Path(rel).parts:
        current /= part
        if current.is_symlink():
            raise OSError(f"transaction path is or contains a symlink: {rel}")


def _link_target_text(link: Path) -> str:
    """The link's own target text. Never resolved, never opened."""
    try:
        return os.readlink(link)
    except OSError as exc:
        return f"<unreadable link target: {type(exc).__name__}>"


def _first_symlink_component(target_dir: Path, rel: str) -> Path | None:
    """The first symlink on the way to *rel* (``lstat`` only), or ``None``."""
    current = target_dir
    for part in Path(rel).parts:
        current /= part
        if current.is_symlink():
            return current
    return None


def _collect_transaction_symlinks(target_dir: Path, *, denied_is_marker: bool = False) -> list[tuple[str, str]]:
    """Every symlinked managed directory or surface as ``(relative path, link target)``, in walk order.

    The same rules :func:`_validate_transaction_surface` has always enforced (a symlinked ancestor of a managed
    root or file; a symlinked directory below a managed root, pruned nested runtime dirs excluded; symlinked
    FILES inside a managed dir stay allowed), but collected in full instead of stopping at the first.
    """
    found: dict[str, str] = {}

    def note(link: Path) -> None:
        found.setdefault(str(link.relative_to(target_dir)), _link_target_text(link))

    for rel in _TRANSACTION_DIRS:
        if (link := _first_symlink_component(target_dir, rel)) is not None:
            note(link)
            continue
        root = target_dir / rel
        if not root.is_dir():
            continue
        for dirpath, dirnames, _filenames in os.walk(root, followlinks=False):
            base = Path(dirpath)
            # Prune non-managed nested runtime dirs (worktrees / node_modules /
            # venvs / nested repos) so os.walk neither descends into them nor
            # flags their symlinks.
            dirnames[:] = [
                d
                for d in dirnames
                if not (
                    _is_pruned_nested_dir(base / d, target_dir, denied_is_marker=denied_is_marker)
                    or _is_user_skill_link(base / d, target_dir)
                )
            ]
            # Reject only symlinked DIRECTORIES: a symlinked dir could redirect a
            # recursive managed write. Symlink FILES are allowed — they commonly
            # appear as unmanaged client runtime state (e.g. .antigravitycli session
            # json) — because park_surface_links keeps writers from following them.
            for name in dirnames:
                if (base / name).is_symlink():
                    note(base / name)
    for rel in _TRANSACTION_FILES:
        if (link := _first_symlink_component(target_dir, rel)) is not None:
            note(link)
    return list(found.items())


def _symlink_kind(target_dir: Path, links: list[tuple[str, str]]) -> str:
    """``directory``, ``file`` or ``file or directory``: what each refused link points at, for the Fix line."""
    kinds = {"directory" if (target_dir / rel).is_dir() else "file" for rel, _link_target in links}
    return kinds.pop() if len(kinds) == 1 else "file or directory"


def _validate_transaction_surface(target_dir: Path, *, denied_is_marker: bool = False) -> None:
    """Fail closed before update/restore when any managed path is redirected.

    Refuses once, naming every symlink and its target and how to fix it, so the user is not walked through
    them one failed run at a time. Nothing is followed, resolved or touched. A restore passes
    *denied_is_marker*: an unreadable nested dir is then kept as it is rather than aborting the rollback.
    """
    if target_dir.is_symlink():
        raise OSError("update target is a symlink")
    links = _collect_transaction_symlinks(target_dir, denied_is_marker=denied_is_marker)
    if not links:
        return
    listing = "\n".join(f"  {rel} -> {link_target}" for rel, link_target in links)
    raise OSError(
        "transaction directory contains a symlinked directory (or a managed surface is a symlink); "
        "TRW never writes through symlinks and updated nothing:\n"
        f"{listing}\n"
        f"Fix: replace each symlink with a real {_symlink_kind(target_dir, links)} (copy its contents in), then re-run."
    )


def _refuse_special_managed_files(target_dir: Path) -> None:
    """Fail closed, before any write, when a path update reads or writes is a FIFO, socket or device.

    The snapshot skips special files so an unrelated one (a stale skill's leftover pipe) never aborts the update,
    but a reader of a MANAGED path would block forever on a FIFO. The fixed surfaces are checked first because
    the manifest listing the recorded ones is itself one of them.
    """
    from ._version_manifest import _manifest_content_hashes, _manifest_key_path, _read_manifest

    special = [rel for rel in _TRANSACTION_FILES if _is_special_file(target_dir / rel)]
    if not special:
        recorded = sorted(
            {_manifest_key_path(key) for key in _manifest_content_hashes(_read_manifest(target_dir)) or {}}
        )
        special = [rel for rel in recorded if _is_special_file(target_dir / rel)]
    if special:
        raise OSError(
            "a TRW-managed path is a named pipe, socket or device file, which update cannot read; "
            "TRW updated nothing:\n" + "\n".join(f"  {printable(rel)}" for rel in special) + "\n"
            "Fix: move each one out of the way (it is not TRW's file), then re-run."
        )


def _snapshot_transaction_paths(target_dir: Path) -> Path:
    _validate_transaction_surface(target_dir)
    _refuse_git_marked_owned_dirs(target_dir)
    _refuse_special_managed_files(target_dir)
    snapshot_root = new_snapshot_dir(target_dir)  # the TRW user directory: durable, one trusted location
    try:
        for rel in (*_TRANSACTION_DIRS, *_TRANSACTION_FILES):
            _reject_symlink_path(target_dir, rel)
            src = target_dir / rel
            if (not src.exists() and not src.is_symlink()) or _is_special_file(src):
                continue
            dest = snapshot_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir() and not src.is_symlink():
                shutil.copytree(src, dest, symlinks=True, ignore=_snapshot_copy_ignore(target_dir))
            else:
                shutil.copy2(src, dest, follow_symlinks=False)
    except OSError:
        release_snapshot(snapshot_root)
        remove_tree(snapshot_root, purpose="unfinished update snapshot")
        raise
    return snapshot_root


def _is_surface_path(rel: str) -> bool:
    """True when repo-relative *rel* lies inside the transaction surface."""
    return rel in _TRANSACTION_FILES or any(rel.startswith(f"{root}/") for root in _TRANSACTION_DIRS)


def _file_signature(path: Path) -> tuple[str, int, bytes] | None:
    """``(kind, mode, content)`` of a surface file, or ``None`` when absent.

    mtime is deliberately not part of it: a rewrite of identical bytes is not a
    change (PRD-INFRA-190 FR02 boundary semantics).
    """
    if path.is_symlink():
        return ("link", 0, os.readlink(path).encode())
    if not path.is_file():
        return None
    return ("file", stat.S_IMODE(path.stat().st_mode), path.read_bytes())


def _surface_files(root: Path, *, denied_is_marker: bool = False) -> set[str]:
    """Repo-relative paths of every file or symlink in *root*'s transaction surface.

    *denied_is_marker* treats an unreadable nested dir as pruned, as the rollback must (it never scanned it).
    """
    found = {rel for rel in _TRANSACTION_FILES if (root / rel).is_file() or (root / rel).is_symlink()}
    for rel_dir in _TRANSACTION_DIRS:
        top = root / rel_dir
        if not top.is_dir() or top.is_symlink():
            continue
        for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
            base = Path(dirpath)
            dirnames[:] = [
                d for d in dirnames if not _is_pruned_nested_dir(base / d, root, denied_is_marker=denied_is_marker)
            ]
            found.update((base / name).relative_to(root).as_posix() for name in filenames)
            found.update((base / d).relative_to(root).as_posix() for d in dirnames if (base / d).is_symlink())
    return found


def _diff_transaction_paths(before_root: Path, after_root: Path) -> dict[str, str]:
    """``{repo-relative path: created|updated|deleted}`` across the transaction surface.

    The one source of update-project's changed-file report, in both modes.
    """
    changes: dict[str, str] = {}
    for rel in sorted(_surface_files(before_root) | _surface_files(after_root)):
        before = _file_signature(before_root / rel)
        after = _file_signature(after_root / rel)
        if before == after:
            continue
        changes[rel] = "created" if before is None else "deleted" if after is None else "updated"
    return changes


def _restore_transaction_file(target_dir: Path, snapshot_root: Path, rel: str, notes: list[str] | None = None) -> None:
    """Put one surface file back to its snapshot state (restored, or removed if it was absent).

    The name is cleared only on proof (:func:`remove_proven_or_keep`); a name that cannot be cleared is kept
    as it is, named in *notes*, and the snapshot copy is put only into an absent name.
    """
    from ._restore_proof import Cleared, put_back_or_preserve, remove_proven_or_keep

    _reject_symlink_path(target_dir, rel)
    sink = notes if notes is not None else []
    settled = remove_proven_or_keep(target_dir, snapshot_root, rel, sink) is Cleared.INTACT
    if not settled and not put_back_or_preserve(target_dir, snapshot_root, rel, sink):
        raise OSError(f"pre-update copy of {rel} not saved in the project")  # the caller keeps the snapshot


def park_surface_links(root: Path) -> list[str]:
    """Unlink every surface symlink so no writer writes through it (PRD-INFRA-190 FR02).

    A link can resolve outside the project, or — in the dry-run scratch copy — back
    into the real target. The transaction snapshot still holds each link and
    :func:`unpark_surface_links` puts it back. Both modes run this, so a dry run
    reports what the real run does.
    """
    parked = sorted(rel for rel in _surface_files(root) if (root / rel).is_symlink())
    for rel in parked:
        (root / rel).unlink()
    return parked


def unpark_surface_links(root: Path, snapshot_root: Path, parked: list[str], result: dict[str, list[str]]) -> None:
    """Restore each parked link, discarding (and reporting) whatever a writer put in its place."""
    for rel in parked:
        if (root / rel).exists() or (root / rel).is_symlink():
            result.setdefault("preserved", []).append(f"{rel} (symlink)")
        _restore_transaction_file(root, snapshot_root, rel, result.setdefault("warnings", []))


#: Analytics inputs the instruction render reads. They sit outside the
#: transaction surface, so the dry-run scratch tree needs its own copy of them.
_RENDER_INPUT_DIRS: tuple[str, ...] = (".trw/context",)


def dirty_state(
    target_dir: Path, effective_data: Path, result: dict[str, list[str]]
) -> tuple[set[str] | None, list[str]]:
    """``(dirty surface paths, dirty bundle paths)`` from one ``git status`` (PRD-INFRA-190 FR04/FR05).

    The dirty set is ``None`` when git could not answer (NFR01); the bundle list
    is then empty and both checks are reported as unknown.
    """
    from ._version_manifest import git_dirty_paths

    data_root = effective_data.resolve()
    target_root = target_dir.resolve()
    bundle_rel = data_root.relative_to(target_root).as_posix() if data_root.is_relative_to(target_root) else None
    dirty = git_dirty_paths(
        target_dir, [*_TRANSACTION_DIRS, *_TRANSACTION_FILES, *([bundle_rel] if bundle_rel else [])]
    )
    if dirty is None:
        result["warnings"].append(
            "git status unavailable: uncommitted-change and dirty-bundle checks are unknown; "
            "only the managed-artifacts hash guard protects edited files"
        )
        return None, []
    bundle_dirty = sorted(p for p in dirty if bundle_rel and p.startswith(f"{bundle_rel}/"))
    return {p for p in dirty if _is_surface_path(p)}, bundle_dirty


def run_in_scratch(target_dir: Path, result: dict[str, list[str]], apply: Callable[[Path], None]) -> None:
    """Run *apply* against a scratch copy of the surface; the target is never written (FR02).

    The scratch tree is the transaction snapshot plus a ``.git`` marker, the
    render inputs and the checkout's daemon token, so the real update code
    produces the same bytes it would produce in place.
    """
    try:
        scratch = _snapshot_transaction_paths(target_dir)
    except OSError as exc:
        result["errors"].append(f"Failed to copy update targets for dry run: {exc}")
        return
    try:
        # A real repository, so writers that ask git for the top level
        # (REVIEW.md) resolve to the scratch tree exactly as they would in place.
        if subprocess.run(["git", "init", "-q", str(scratch)], capture_output=True, check=False).returncode:  # noqa: S603,S607
            (scratch / ".git").mkdir()
        # Render inputs are copied as content: a copied link could reach the real tree.
        for rel in _RENDER_INPUT_DIRS:
            if (target_dir / rel).is_dir():
                shutil.copytree(target_dir / rel, scratch / rel, ignore_dangling_symlinks=True, dirs_exist_ok=True)
        # The render counts the store through the memory daemon (PRD-CORE-280),
        # which this checkout reaches with its token: without it the scratch render
        # says "not measured" and the dry run reports a change the real run never makes.
        from trw_memory.daemon._grants import CHECKOUT_TOKEN_RELPATH

        token = target_dir / CHECKOUT_TOKEN_RELPATH
        if token.is_file() and not token.is_symlink():
            (scratch / CHECKOUT_TOKEN_RELPATH).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(token, scratch / CHECKOUT_TOKEN_RELPATH)  # keeps the 0600 mode
        with git_view_of(scratch, target_dir):  # the git-clean retire rule must see the real checkout
            apply(scratch)
    finally:
        remove_tree(scratch, purpose="update dry-run scratch")
    # Writers name absolute paths in their notes; point them at the real target.
    for key, items in result.items():
        result[key] = [
            item.replace(str(scratch.resolve()), str(target_dir)).replace(str(scratch), str(target_dir))
            for item in items
        ]
