"""Filesystem snapshot and rollback support for ``update_project``."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from trw_mcp.canons.registry import install_view, load_registry
from trw_mcp.framework_deployment import DEPLOYMENT_RELATIVE_PATH
from trw_mcp.state.claude_md._sidecar_retire import SIDECAR_RELPATHS

_CANON_REGISTRY = load_registry()
_MANAGED_TRW_FILES: tuple[str, ...] = tuple(
    dict.fromkeys(
        [
            *(destination for _, destination in install_view(_CANON_REGISTRY) if destination.startswith(".trw/")),
            str(DEPLOYMENT_RELATIVE_PATH),
        ]
    )
)
# G2 (installer refinement 5.1.0): the change-counter's file discovery is this
# allow-listed directory scan, and it silently missed `.grok` when grok became
# a client — every file `update-project --ide grok` wrote landed outside the
# diff, so a run that provisioned 12 new files reported "0 created". Audited
# against every root-level UninstallSurface directory in
# `client_profiles.catalog` (test_update_transaction_dirs_cover_every_client
# parametrizes over `builtin_client_ids()` so a future client addition fails
# loudly here instead of repeating this silently).
_TRANSACTION_DIRS: tuple[str, ...] = (
    ".agents",
    ".antigravitycli",
    ".claude",
    ".codex",
    ".cursor",
    ".github",
    ".grok",
    ".opencode",
    ".vscode",
)
_TRANSACTION_FILES: tuple[str, ...] = (
    *_MANAGED_TRW_FILES,
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
    ".trw/runtime/hook-env.sh",
    ".trw/templates/claude_md.md",
    ".trw/frameworks/VERSION.yaml",
    # The retired instruction sidecars (PRD-QUAL-143-FR01): the update deletes
    # them, so a rollback that restores an ``@`` import must restore its target.
    *SIDECAR_RELPATHS,
    ".mcp.json",
    "AGENTS.md",
    "ANTIGRAVITY.md",
    "CLAUDE.md",
    # Root FRAMEWORK.md is a live update target fed by the canon registry.
    "FRAMEWORK.md",
    "opencode.json",
    "REVIEW.md",
)


# Nested runtime/tooling directories that update-project does NOT manage and that
# legitimately contain symlinks: Claude Code agent worktrees (``.claude/worktrees/``),
# any nested git worktree/repo/submodule, and package-manager/build dirs a client
# config tree may nest (e.g. ``.opencode/node_modules/.bin/*`` npm shims, virtualenvs,
# caches). The symlink-escape guard only needs to cover TRW-managed content — scanning
# these subtrees made a stray symlink abort the whole update (reports 2026-07-18:
# ``.claude/worktrees/.../evals/LATEST`` and ``.opencode/node_modules/.bin/node-which``,
# which left FRAMEWORK.md stuck at v25). Pruning is safe: update-project writes nothing
# into these subtrees, so no symlink there can redirect a managed write.
_PRUNED_NESTED_DIR_NAMES: frozenset[str] = frozenset(
    {"worktrees", "node_modules", ".venv", "venv", ".git", "__pycache__", ".next", ".turbo"}
)


def _is_pruned_nested_dir(path: Path) -> bool:
    """A nested directory update-project must neither scan nor snapshot: a Claude
    Code ``worktrees`` container, a package-manager/build dir, or any nested git
    worktree/repo/submodule (a ``.git`` file or dir marks one)."""
    if path.name in _PRUNED_NESTED_DIR_NAMES:
        return True
    dotgit = path / ".git"
    return dotgit.is_file() or dotgit.is_dir()


def _snapshot_copy_ignore(directory: str, names: list[str]) -> set[str]:
    """``shutil.copytree`` ignore callback: skip nested runtime dirs (worktrees /
    nested git repos) so a snapshot never copies unmanaged, symlink-bearing state."""
    base = Path(directory)
    return {name for name in names if _is_pruned_nested_dir(base / name)}


def _remove_transaction_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        # Preserve nested runtime dirs (worktrees / nested git repos) so a
        # rollback that rewrites a managed dir never deletes unmanaged state.
        for child in path.iterdir():
            if child.is_dir() and not child.is_symlink() and _is_pruned_nested_dir(child):
                continue
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                _remove_transaction_path(child)
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


def _validate_transaction_surface(target_dir: Path) -> None:
    """Fail closed before update/restore when any managed path is redirected."""
    if target_dir.is_symlink():
        raise OSError("update target is a symlink")
    for rel in _TRANSACTION_DIRS:
        _reject_symlink_path(target_dir, rel)
        root = target_dir / rel
        if not root.is_dir():
            continue
        for dirpath, dirnames, _filenames in os.walk(root, followlinks=False):
            base = Path(dirpath)
            # Prune non-managed nested runtime dirs (worktrees / node_modules /
            # venvs / nested repos) so os.walk neither descends into them nor
            # flags their symlinks.
            dirnames[:] = [d for d in dirnames if not _is_pruned_nested_dir(base / d)]
            # Reject only symlinked DIRECTORIES: a symlinked dir could redirect a
            # recursive managed write. Symlink FILES are allowed — they commonly
            # appear as unmanaged client runtime state (e.g. .antigravitycli session
            # json) — because park_surface_links keeps writers from following them.
            for name in dirnames:
                candidate = base / name
                if candidate.is_symlink():
                    relative = candidate.relative_to(target_dir)
                    raise OSError(f"transaction directory contains a symlinked directory: {relative}")
    for rel in _TRANSACTION_FILES:
        _reject_symlink_path(target_dir, rel)


def _snapshot_transaction_paths(target_dir: Path) -> Path:
    _validate_transaction_surface(target_dir)
    snapshot_root = Path(tempfile.mkdtemp(prefix="trw-update-snapshot-"))
    try:
        for rel in (*_TRANSACTION_DIRS, *_TRANSACTION_FILES):
            _reject_symlink_path(target_dir, rel)
            src = target_dir / rel
            if not src.exists() and not src.is_symlink():
                continue
            dest = snapshot_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir() and not src.is_symlink():
                shutil.copytree(src, dest, symlinks=True, ignore=_snapshot_copy_ignore)
            else:
                shutil.copy2(src, dest, follow_symlinks=False)
    except OSError:
        shutil.rmtree(snapshot_root, ignore_errors=True)
        raise
    return snapshot_root


def _restore_transaction_snapshot(target_dir: Path, snapshot_root: Path) -> None:
    _validate_transaction_surface(target_dir)
    for rel in (*_TRANSACTION_DIRS, *_TRANSACTION_FILES):
        _reject_symlink_path(target_dir, rel)
        dest = target_dir / rel
        src = snapshot_root / rel
        if dest.is_dir() and not dest.is_symlink():
            if src.is_dir() and not src.is_symlink():
                # Snapshot HAD this dir: remove only the MANAGED children,
                # preserving the pruned nested runtime dirs (worktrees / nested
                # repos) that were never snapshotted — else a rollback would
                # delete them — then restore the snapshotted managed content.
                for child in dest.iterdir():
                    if _is_pruned_nested_dir(child):
                        continue
                    _remove_transaction_path(child)
                shutil.copytree(src, dest, symlinks=True, dirs_exist_ok=True, ignore=_snapshot_copy_ignore)
                continue
            # Snapshot did NOT have this dir — it was newly created by the failed
            # update. Remove the managed dir entirely (rmdir once its managed
            # children are gone), preserving only any pruned nested runtime dirs.
            _remove_transaction_path(dest)
            continue
        if dest.exists() or dest.is_symlink():
            _remove_transaction_path(dest)
        if not src.exists() and not src.is_symlink():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir() and not src.is_symlink():
            shutil.copytree(src, dest, symlinks=True, ignore=_snapshot_copy_ignore)
        else:
            shutil.copy2(src, dest, follow_symlinks=False)


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


def _surface_files(root: Path) -> set[str]:
    """Repo-relative paths of every file or symlink in *root*'s transaction surface."""
    found = {rel for rel in _TRANSACTION_FILES if (root / rel).is_file() or (root / rel).is_symlink()}
    for rel_dir in _TRANSACTION_DIRS:
        top = root / rel_dir
        if not top.is_dir() or top.is_symlink():
            continue
        for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
            base = Path(dirpath)
            dirnames[:] = [d for d in dirnames if not _is_pruned_nested_dir(base / d)]
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


def _restore_transaction_file(target_dir: Path, snapshot_root: Path, rel: str) -> None:
    """Put one surface file back to its snapshot state (restored, or removed if it was absent)."""
    _reject_symlink_path(target_dir, rel)
    dest = target_dir / rel
    src = snapshot_root / rel
    if dest.is_symlink() or dest.is_file():
        dest.unlink()
    if src.is_symlink() or src.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest, follow_symlinks=False)


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
        _restore_transaction_file(root, snapshot_root, rel)


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
        apply(scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    # Writers name absolute paths in their notes; point them at the real target.
    for key, items in result.items():
        result[key] = [
            item.replace(str(scratch.resolve()), str(target_dir)).replace(str(scratch), str(target_dir))
            for item in items
        ]
