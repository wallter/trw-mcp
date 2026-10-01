"""Learning-corpus blast-radius helpers for uninstall.

Belongs to the ``_subcommands_lifecycle.py`` ``_run_uninstall`` facade.
Extracted so the parent module stays under the 350 effective-LOC gate.
Covers both the project-tier ``.trw`` corpus (the ``--keep-memory`` /
destructive-warning path) and the machine-local ``~/.trw`` user-tier store
(PRD-INFRA-192 FR09 P1-e), which reuses the same blast-radius counting.
"""

from __future__ import annotations

import errno
import os
import shlex
import shutil
import stat
from collections.abc import Callable, Iterator
from functools import lru_cache
from importlib import resources
from pathlib import Path

from trw_mcp.bootstrap._safe_remove import TRASH_DIR_NAME, safe_remove
from trw_mcp.bootstrap._utils import printable

# Subpaths of a ``.trw`` dir that hold the durable learning corpus.
# ``--keep-memory`` preserves these; the blast-radius warning is gated on them.
# ``memory.db`` is the authoritative SQLite store (a FILE, not the memory/ dir),
# so it MUST be preserved alongside the learning entry files.
_MEMORY_SUBPATHS: tuple[str, ...] = ("memory", "memory.db", "learnings")


def _finish_trash(trw_dir: Path, *, remove_trw_dir: bool) -> bool:
    """``rmdir`` an empty ``.trw/trash`` then (optionally) an empty *trw_dir*; report a non-empty trash.

    Only ``rmdir`` is used, so a capture that landed after the caller's directory listing keeps both
    directories (ENOTEMPTY). Returns True when *trw_dir* itself was removed.
    """
    trash = trw_dir / TRASH_DIR_NAME
    try:
        os.rmdir(trash)
    except FileNotFoundError:  # trw-fail-silent-allow: no trash directory means nothing to keep
        pass
    except OSError:  # trw-fail-silent-allow: a non-empty trash is reported below and kept
        if trash.is_symlink() or not trash.is_dir():
            print("  Kept .trw/trash: it is not a directory TRW created; remove it yourself if unneeded")
            return False
        try:
            held = sum(1 for _ in os.scandir(trash))
        except OSError:  # trw-fail-silent-allow: unreadable trash is still kept; the count is cosmetic
            held = 0
        print(
            f"  Kept .trw/trash: it holds {held} backup(s) TRW could not remove automatically "
            f"(see `trw-mcp doctor`; remove with: rm -rf {shlex.quote(str(trash))})"
        )
        return False
    if not remove_trw_dir:
        return False
    try:
        os.rmdir(trw_dir)
    except OSError as exc:  # trw-fail-silent-allow: ENOTEMPTY keeps .trw; other errors are printed
        if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
            print(f"  Kept {trw_dir.name}: {exc}")
        return False
    return True


def _remove_children(trw_dir: Path, keep: Callable[[str], bool]) -> Iterator[tuple[str, str | None]]:
    """Remove each child of *trw_dir* not kept, relative to ONE ``O_NOFOLLOW`` directory fd.

    Anchoring on the fd means a ``.trw`` swapped for a symlink mid-uninstall cannot redirect a
    deletion outside the project. Yields ``(name, failure)``; a symlinked child is refused, not
    unlinked. Raises ``OSError`` when *trw_dir* itself is a symlink or not a directory.
    """
    fd = os.open(trw_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in sorted(os.listdir(fd)):
            if keep(name):
                continue
            try:
                mode = os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode):
                    yield name, "refused: path is a symlink"
                    continue
                if stat.S_ISDIR(mode):
                    shutil.rmtree(name, dir_fd=fd)  # fd-based walk; refuses a symlink swapped in
                else:
                    os.unlink(name, dir_fd=fd)
            except FileNotFoundError:  # trw-fail-silent-allow: already gone is the goal
                continue
            except OSError as exc:
                yield name, f"error removing {name}: {exc}"
                continue
            yield name, None
    finally:
        os.close(fd)


def remove_trw_dir(trw_dir: Path, target: Path, display: Callable[[Path, Path], str]) -> tuple[int, int]:
    """Remove everything under *trw_dir* except ``trash``, then the emptied directories.

    The whole-project (no ``--keep-memory``) counterpart of :func:`keep_memory_in_dir`. Returns
    ``(removed, errors)`` where ``removed`` is 1 when *trw_dir* itself is gone.
    """
    errors = 0
    try:
        for name, failure in _remove_children(trw_dir, lambda n: n.casefold() == TRASH_DIR_NAME):
            if failure:
                errors += 1
                print(f"  Error removing {display(trw_dir / name, target)}: {failure}")
    except OSError as exc:  # .trw is a symlink or not a directory: refuse, touch nothing
        print(f"  Error removing {display(trw_dir, target)}: refused: {exc}")
        return 0, 1
    gone = _finish_trash(trw_dir, remove_trw_dir=errors == 0)
    if gone:
        print(f"  Removed: {display(trw_dir, target)}")
    return (1 if gone else 0), errors


#: Top-level ``.trw/`` names TRW creates lazily at runtime and that no scaffold
#: list, config default or ``gitignore.txt`` line names (found by scanning the
#: package for ``trw_dir / "<name>"`` literals). Guarded by
#: ``tests/test_uninstall_user_files.py::test_known_set_covers_source_literals``.
_LAZY_TRW_ENTRIES: frozenset[str] = frozenset(
    {
        ".gitignore", "config.yaml", "installer-meta.yaml", "managed-artifacts.yaml", "installed-version.json",
        "entitlements.yaml", "sync-state.json", "sync-replay.jsonl", "upgrade_events.jsonl",
        "deliver-deferred.lock", "runtime", "channels", "security", "registry", "overrides",
        "delivery", "cache", "profiles", "distill", "telemetry", "memory", "memory.db", "learnings",
        "state", "meta", "meta_tune", "findings", "feedback", "requirements", "worktrees",
        "hooks", "intel-cache.json", "code-index", "backups", "INSTRUCTIONS.md", "client-profile.env",
    }
)  # fmt: skip
_TRW_GENERATED_SUFFIXES: tuple[str, ...] = (".lock", ".jsonl", ".db", ".retired")


@lru_cache(maxsize=1)
def trw_created_names() -> frozenset[str]:
    """Top-level ``.trw/`` names TRW itself creates, derived from its own code/data.

    Union of: the ``init-project`` scaffold (``bootstrap._TRW_DIRS``), the path
    defaults in ``TRWConfig``, the first path component of every ``.trw``
    ``gitignore.txt`` rule, the learning-corpus names, and ``_LAZY_TRW_ENTRIES``.
    """
    from trw_mcp.bootstrap import _TRW_DIRS
    from trw_mcp.models.config import TRWConfig

    names: set[str] = set(_LAZY_TRW_ENTRIES) | set(_MEMORY_SUBPATHS) | {TRASH_DIR_NAME}
    for rel in _TRW_DIRS:
        parts = Path(rel).parts
        if parts[0] == ".trw" and len(parts) > 1:
            names.add(parts[1])
    for fname, field in TRWConfig.model_fields.items():
        default = field.default
        if not isinstance(default, str) or not fname.endswith(("_dir", "_file", "_root")):
            continue
        parts = Path(default).parts
        if parts and parts[0] == ".trw":
            parts = parts[1:]
        elif fname in ("task_root", "trw_dir") or default.startswith(("/", "..")):
            continue
        if parts:
            names.add(parts[0])
    ignore = resources.files("trw_mcp").joinpath("data/gitignore.txt").read_text(encoding="utf-8")
    for line in ignore.splitlines():
        rule = line.strip()
        if rule and not rule.startswith(("#", "!", "*")):
            names.add(Path(rule).parts[0])
    return frozenset(names)


def untracked_trw_entries(trw_dir: Path) -> list[str]:
    """Top-level entries of ``trw_dir`` that TRW does not create, formatted for display.

    A directory shows its file count (no per-file recursion in the listing);
    the corpus names and SQLite sidecars ``--keep-memory`` preserves are never listed.
    """

    known = trw_created_names()
    out: list[str] = []
    try:
        entries = sorted(trw_dir.iterdir(), key=lambda e: e.name)
    except OSError:  # the listing is advisory: never abort an uninstall over it
        return out
    for entry in entries:
        name = entry.name
        # Deliberate under-report: files with these suffixes are still removed, just not listed.
        if name in known or name.endswith(_TRW_GENERATED_SUFFIXES) or ".retired" in name:
            continue
        if preserved_by_keep_memory(name):  # TRW's own corpus + SQLite sidecars, kept or not
            continue
        if name.casefold() == TRASH_DIR_NAME:  # trash holds captured user bytes; its own line reports it
            continue
        shown = printable(name)
        if entry.is_dir() and not entry.is_symlink():
            try:
                count = str(sum(1 for f in entry.rglob("*") if f.is_file()))
            except OSError:  # vanished (or unreadable) mid-walk
                count = "?"
            out.append(f"{shown}/ ({count} files)")
        else:
            out.append(shown)
    return out


def print_untracked_trw_entries(trw_dir: Path) -> None:
    """Print the one-line ``.trw/`` user-file notice (nothing when there are none)."""
    if not trw_dir.is_dir():
        return
    items = untracked_trw_entries(trw_dir)
    if items:
        print(f"\n  {len(items)} item(s) in .trw/ were not created by TRW and will be removed: {', '.join(items)}")


def preserved_by_keep_memory(name: str) -> bool:
    """True for the top-level ``.trw`` names ``--keep-memory`` keeps: the corpus
    names plus SQLite sidecars (``memory.db-wal`` / ``memory.db-shm``)."""
    return name in _MEMORY_SUBPATHS or name.startswith("memory.db")


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

    for child in sorted(trw_dir.iterdir()):
        # Preserve the corpus dirs/files plus SQLite sidecars (memory.db-wal /
        # memory.db-shm) so the kept DB reopens cleanly; ``trash`` is only ever rmdir'd below.
        if preserved_by_keep_memory(child.name) or child.name.casefold() == TRASH_DIR_NAME:
            continue
        is_real_dir = child.is_dir() and not child.is_symlink()
        failure = safe_remove(child, trw_dir, expect="dir" if is_real_dir else "file")  # re-lstat at the act
        if failure:
            errors += 1
            print(f"  Error removing {display(child, target)}: {failure}")
        else:
            removed += 1
            print(f"  Removed: {display(child, target)}")
    _finish_trash(trw_dir, remove_trw_dir=False)
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
