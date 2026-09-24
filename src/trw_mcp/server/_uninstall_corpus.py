"""Learning-corpus blast-radius helpers for uninstall.

Belongs to the ``_subcommands_lifecycle.py`` ``_run_uninstall`` facade.
Extracted so the parent module stays under the 350 effective-LOC gate.
Covers both the project-tier ``.trw`` corpus (the ``--keep-memory`` /
destructive-warning path) and the machine-local ``~/.trw`` user-tier store
(PRD-INFRA-192 FR09 P1-e), which reuses the same blast-radius counting.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from trw_mcp.bootstrap._safe_remove import path_refusal

# Subpaths of a ``.trw`` dir that hold the durable learning corpus.
# ``--keep-memory`` preserves these; the blast-radius warning is gated on them.
# ``memory.db`` is the authoritative SQLite store (a FILE, not the memory/ dir),
# so it MUST be preserved alongside the learning entry files.
_MEMORY_SUBPATHS: tuple[str, ...] = ("memory", "memory.db", "learnings")


def count_learnings(trw_dir: Path) -> int:
    """Best-effort count of learning entry files under ``<trw_dir>/learnings``.

    Counts ``.yaml`` files under ``learnings/`` (and its ``entries/`` subdir),
    excluding the ``index.yaml`` seed. A missing directory yields 0. This is a
    rough blast-radius figure for the destructive-uninstall warning, not an
    exact corpus size (the authoritative store is ``memory.db``).
    """
    learnings = trw_dir / "learnings"
    if not learnings.is_dir():
        return 0
    return sum(1 for p in learnings.rglob("*.yaml") if p.is_file() and p.name != "index.yaml")


def trw_corpus_blast_radius(trw_dir: Path) -> tuple[bool, int]:
    """Return ``(has_corpus, learning_count)`` for a ``.trw`` dir (project or user-tier).

    ``has_corpus`` is True when the store's ``memory/`` directory exists (it
    holds ``memory/memory.db``), a flat ``memory.db`` exists, OR any learning
    entry files are present -- i.e. removing this dir would permanently destroy
    the accumulated learning corpus. Any ``memory/`` directory counts, so
    ``--keep-memory`` is honoured whenever it could matter.
    """
    has_db = (trw_dir / "memory").is_dir() or (trw_dir / "memory.db").is_file()
    count = count_learnings(trw_dir)
    return (has_db or count > 0), count


def keep_memory_in_dir(trw_dir: Path, target: Path, display: Callable[[Path, Path], str]) -> tuple[int, int]:
    """Remove everything under *trw_dir* EXCEPT memory/ and learnings/.

    Implements ``--keep-memory``: the durable learning corpus
    (``.trw/memory`` + ``.trw/learnings``) is preserved while all other
    session/config state is removed. Returns ``(removed, errors)`` counts of
    top-level entries. The ``.trw`` dir itself is preserved (it still holds
    the corpus). Each child is refused (not touched) rather than removed when
    it is itself a symlink -- same rule as every other uninstall deletion path.
    """
    removed = 0
    errors = 0
    preserved = {trw_dir / name for name in _MEMORY_SUBPATHS}
    for child in sorted(trw_dir.iterdir()):
        # Preserve the corpus dirs/files plus SQLite sidecars (memory.db-wal /
        # memory.db-shm) so the kept DB reopens cleanly.
        if child in preserved or child.name.startswith("memory.db"):
            continue
        refusal = path_refusal(child, trw_dir)
        if refusal:
            errors += 1
            print(f"  Error removing {display(child, target)}: {refusal}")
            continue
        try:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
            removed += 1
            print(f"  Removed: {display(child, target)}")
        except OSError as exc:
            errors += 1
            print(f"  Error removing {display(child, target)}: {exc}")
    return removed, errors


def print_corpus_warning(
    trw_dir: Path, learning_count: int, target: Path, display: Callable[[Path, Path], str]
) -> None:
    """Print the destructive-uninstall blast-radius warning + export nudge.

    Names exactly what is about to be permanently destroyed (memory.db + the
    learning count) and nudges an export-first, since the learning corpus is
    TRW's core durable value and cannot be recovered after rmtree.
    """
    has_db = (trw_dir / "memory.db").is_file()
    pieces: list[str] = []
    if has_db:
        pieces.append("memory.db")
    if learning_count > 0:
        pieces.append(f"{learning_count} learning(s)")
    blast = " and ".join(pieces) if pieces else "the learning corpus"
    rel = display(trw_dir, target)
    print()
    print("  WARNING: this permanently deletes your learning corpus.")
    print(f"    {rel} contains {blast} — removing it CANNOT be undone.")
    print("    Export first:  trw-mcp export --scope learnings --output learnings.json")
    print("    Or keep it:    re-run with --keep-memory to preserve memory/ + learnings/.")
