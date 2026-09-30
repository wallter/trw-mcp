"""Make a restored store and the project's derived copies of it agree (INC-128).

Restore swaps the memory store only. A project also keeps copies derived from it: the warm-tier keyword sidecar and its
vector index (``.trw/memory/warm.jsonl``, ``warm.db``, which recall reads) and the per-learning YAML files
(``.trw/learnings/entries``, ``index.yaml``). Left alone they still hold learnings newer than the backup and recall
returns them, so the store and recall disagree. After the swap they are MOVED ASIDE, never deleted, into
``.trw/pre-restore-derived-<time>/``: the rows they held were in the store that restore archived first (unless
``--no-snapshot``), and the move is undone with one ``mv``. A symlinked tier is left and named, not followed.
``--keep-derived`` leaves everything and prints what it left.
"""

from __future__ import annotations

import os
import shlex
import time
from pathlib import Path

#: Paths under ``.trw`` that are derived from the store, relative to it.
_DERIVED = (
    Path("memory") / "warm.jsonl",
    Path("memory") / "warm.db",
    Path("memory") / "warm.db-wal",
    Path("memory") / "warm.db-shm",
    Path("learnings") / "entries",
    Path("learnings") / "index.yaml",
)


def _present(trw_dir: Path) -> list[Path]:
    return [rel for rel in _DERIVED if (trw_dir / rel).exists() or (trw_dir / rel).is_symlink()]


def _store_warm_paths(store_dir: Path) -> list[Path]:
    """The warm-tier files each project keeps BESIDE the store: ``project_*/memory/warm.{jsonl,db,db-wal,db-shm}``.

    The daemon's per-project tiers live under the store's own directory, not the project's ``.trw``; recall reads them,
    and they hold every learning the project ever demoted, including ones newer than a backup (INC-128).
    """
    found: list[Path] = []
    for project in sorted(store_dir.glob("project_*")):
        for name in ("warm.jsonl", "warm.db", "warm.db-wal", "warm.db-shm"):
            rel = project.relative_to(store_dir) / "memory" / name
            if (store_dir / rel).exists() or (store_dir / rel).is_symlink():
                found.append(rel)
    return found


def _describe(trw_dir: Path, rel: Path) -> str:
    path = trw_dir / rel
    if path.is_dir():
        return f"{rel}/ ({sum(1 for _ in path.glob('*.yaml'))} learning file(s))"
    return str(rel)


def restore_derived_tiers(trw_dir: Path | None, *, keep: bool) -> str | None:
    """Move the project's derived tiers aside (or, with *keep*, just name them); the text to print, or ``None``."""
    if trw_dir is None or not trw_dir.is_dir():
        return None
    return _handle(trw_dir, _present(trw_dir), keep=keep)


def restore_store_warm_tiers(store_dir: Path, *, keep: bool) -> str | None:
    """The same for the per-project warm tiers that live beside the restored store."""
    if not store_dir.is_dir():
        return None
    return _handle(store_dir, _store_warm_paths(store_dir), keep=keep)


def _handle(trw_dir: Path, present: list[Path], *, keep: bool) -> str | None:
    """Move *present* (paths relative to *trw_dir*) into a fresh ``pre-restore-derived-*`` directory, or name them.

    Advisory after a completed restore: an OS error on one tier is reported for that tier and never raised.
    """
    if not present:
        return None
    if keep:
        names = "\n".join(f"  - {_describe(trw_dir, rel)}" for rel in present)
        return (
            f"NOT restored (--keep-derived): these derived copies in {trw_dir} were left as they were and may hold "
            f"learnings newer than this backup, which recall can still return:\n{names}"
        )
    aside = trw_dir / f"pre-restore-derived-{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"
    moved: list[str] = []
    skipped: list[str] = []
    try:
        aside.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        return f"Derived copies in {trw_dir} were NOT moved aside ({type(exc).__name__}); recall may still return learnings newer than this backup."
    for rel in present:
        source = trw_dir / rel
        if source.is_symlink():
            skipped.append(f"{rel} (a symlink, not followed)")
            continue
        try:
            target = aside / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            description = _describe(trw_dir, rel)
            source.rename(target)
            if rel.name == "entries":
                source.mkdir()  # the learn path expects the directory to exist
            moved.append(description)
        except OSError as exc:
            skipped.append(f"{rel} ({type(exc).__name__})")
    lines = [
        f"Moved aside (not deleted) so recall matches the restored store: {', '.join(moved) or 'nothing'}",
        f"  to {aside}; to undo: cp -Rp {shlex.quote(str(aside))}/. {shlex.quote(str(trw_dir))}/ (copies everything back; the moved-aside copy stays),",
    ]
    if skipped:
        lines.append(f"  left in place: {', '.join(skipped)}")
    return "\n".join(lines)
