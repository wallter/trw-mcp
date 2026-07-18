"""Filesystem snapshot and rollback support for ``update_project``."""

from __future__ import annotations

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


def _remove_transaction_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


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
        for descendant in root.rglob("*"):
            if descendant.is_symlink():
                relative = descendant.relative_to(target_dir)
                raise OSError(f"transaction directory contains a symlink: {relative}")
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
                shutil.copytree(src, dest, symlinks=True)
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
        if dest.exists() or dest.is_symlink():
            _remove_transaction_path(dest)
        src = snapshot_root / rel
        if not src.exists() and not src.is_symlink():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir() and not src.is_symlink():
            shutil.copytree(src, dest, symlinks=True)
        else:
            shutil.copy2(src, dest, follow_symlinks=False)
