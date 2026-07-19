"""Filesystem snapshot and rollback support for ``update_project``."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from trw_mcp.canons.registry import install_view, load_registry
from trw_mcp.framework_deployment import DEPLOYMENT_RELATIVE_PATH

_CANON_REGISTRY = load_registry()
_MANAGED_TRW_FILES: tuple[str, ...] = tuple(
    dict.fromkeys(
        [
            *(destination for _, destination in install_view(_CANON_REGISTRY) if destination.startswith(".trw/")),
            *(
                destination
                for canon in _CANON_REGISTRY.compiled_canons
                for destination in (canon.runtime_compact_core, canon.runtime_reference)
            ),
            str(DEPLOYMENT_RELATIVE_PATH),
        ]
    )
)
_TRANSACTION_DIRS: tuple[str, ...] = (
    ".agents",
    ".claude",
    ".codex",
    ".cursor",
    ".opencode",
    ".vscode",
    ".github",
    ".gemini",
    ".antigravitycli",
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
    ".trw/context/behavioral_protocol.md",
    ".trw/context/behavioral_protocol.yaml",
    ".trw/context/messages.yaml",
    ".trw/credentials.yaml",
    ".trw/installer-meta.yaml",
    ".trw/managed-artifacts.yaml",
    ".trw/runtime/hook-env.sh",
    ".trw/templates/claude_md.md",
    ".trw/frameworks/VERSION.yaml",
    ".mcp.json",
    "AGENTS.md",
    "ANTIGRAVITY.md",
    "CLAUDE.md",
    # Root FRAMEWORK.md is a live update target fed by the canon registry.
    "FRAMEWORK.md",
    "GEMINI.md",
    "opencode.json",
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
                shutil.rmtree(child)
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
            # recursive managed write. Symlink FILES are safe — the snapshot copies
            # them with follow_symlinks=False and the framework writer guards its
            # own targets — and commonly appear as unmanaged client runtime state
            # (e.g. .antigravitycli session json), so scanning them would abort the
            # update for no security benefit.
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
                shutil.copytree(
                    src, dest, symlinks=True, dirs_exist_ok=True, ignore=_snapshot_copy_ignore
                )
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
