"""One-time repair of anchors stored with a machine path (E2E-INC-025).

Belongs to the deliver-time maintenance in ``tools/_ceremony_deliver_steps.py``.

Before the fix, ``trw_learn`` persisted an anchor as the ABSOLUTE path of the
edited file with its leading ``/`` stripped (``Users/<name>/proj/src/a.py``).
That key never matched the repo-relative one the pre-edit hint looks up, and it
carried the machine's directory layout (including ``$HOME``) into a row that can
sync off the machine. Matching alone would leave that path stored, so rows are
repaired. Only these rules may ever change a key; a stale anchor to a deleted source is
normal and stays, so only rule 3 looks at whether a file exists:

1. REWRITE: the key starts with this checkout's root (resolved or not, leading
   ``/`` stripped); it becomes repo-relative.
2. WORKTREE: a (post-rule-1, or already relative) key shaped
   ``.claude|.trw/worktrees/<id>/<tail>`` becomes ``<tail>``: the worktree is
   deleted after its branch lands, the tail is the file the anchor meant.
3. DROP a temp path: a key that is not under this checkout but lies under a temp
   root (``/private/tmp``, ``/var/folders``, the resolved ``gettempdir()`` unless it is
   bare ``/tmp``, slash-stripped)
   and does not exist under the root is a session scratch file, dead by now.
4. DROP: the key's own shape proves an absolute origin: it starts with ``/``.
   Nothing else is provable: ``C:/src/a.py`` is a valid relative POSIX path, so a
   drive-letter shape is kept and counted as ambiguous (codex r3), never dropped.

Rows are never deleted, only their anchors, and every rewritten row also gets
``anchor_validity`` recomputed in the same write.

Everything else is KEPT, always. A spelling that merely resembles a machine path
(``Users/<x>/...``, ``home/<x>/...``, ``tmp/...``, the home directory minus its
slash) is not provenance: ``srv/alice/x.py`` is a valid relative key on a machine
whose home is ``/srv/alice``. Such a key that is machine-shaped and absent under
the root is logged and counted as ambiguous. Ambiguity does NOT hold the marker:
the classification is stable, so rescanning could never resolve it, and holding
would rescan every deliver forever. The count reaches the operator in the deliver
result (``RepairOutcome.review_note``). New writes are already repo-relative, so
this set can only shrink over time.

The marker ``.trw/context/anchors_repo_relative`` (content ``done v2``; the
version-1 marker was ``done``, so a store that finished v1 reruns once) is written after a COMPLETE,
CLEAN pass: every row paged (``_PAGE`` per deliver, offset in
``.trw/context/anchors_repair_cursor``), no write conflict or store error, and a
final tail scan of every row updated since the pass began (newest-first paging
lets a row inserted mid-pass slip past the offset) that itself changes nothing.
Each row is written under ``if_revision``, so a concurrent anchor edit is never
overwritten.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import structlog
from trw_memory.models.memory import Anchor, MemoryEntry

if TYPE_CHECKING:
    from trw_mcp.state._store_selection import MemoryStore

logger = structlog.get_logger(__name__)

_MARKER = Path("context") / "anchors_repo_relative"
_CURSOR = Path("context") / "anchors_repair_cursor"
_PAGE = 5000
_CONFLICT_RETRIES = 3
# First component of a machine-shaped key; only ever used to log ambiguity, never to change a key.
_MACHINE_ISH = frozenset({"Users", "home", "private", "var", "tmp", "root", "Volumes", "mnt", "opt"})
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_TAIL_PASSES = 3
_VERSION = 2  # 1: root-prefix rewrite and absolute drop; 2: worktree segment, temp drop, validity refresh
_MARKER_TEXT = f"done v{_VERSION}\n"
_WORKTREE = re.compile(r"^\.(?:claude|trw)/worktrees/[^/]+/(.+)$")
# Bare ``tmp/`` is not here: it is a plausible repo directory, so trunk keeps it as ambiguous (codex r2).
_TEMP_ROOTS = ("private/tmp", "var/folders", "private/var/folders")


def _under_temp_root(bare: str) -> bool:
    """*bare* (a slash-stripped key) lies under a temp root, ``gettempdir()`` included."""
    gettemp = str(Path(tempfile.gettempdir()).resolve()).lstrip("/")
    roots = {*_TEMP_ROOTS, *((gettemp,) if gettemp != "tmp" else ())}
    return any(bare == root or bare.startswith(root + "/") for root in roots)


def _repaired_file(file: str, project_root: Path) -> tuple[str | None, bool, bool]:
    """``(new file or None to drop, changed, ambiguous)`` for one stored anchor ``file``.

    A drop is "absolute" when ``file`` starts with ``/``, else a temp-root drop.
    """
    bare = file.lstrip("/")
    under_root = False
    for root in dict.fromkeys((project_root, project_root.resolve())):
        prefix = str(root).lstrip("/") + "/"
        if bare.startswith(prefix) and len(bare) > len(prefix):
            file, under_root = bare[len(prefix) :], True  # rule 1
            break
    if worktree := _WORKTREE.match(file):
        return worktree.group(1), True, False  # rule 2
    if under_root:
        return file, True, False
    if file.startswith("/"):
        return None, True, False  # rule 4: the only shape no relative POSIX path can have
    if _under_temp_root(bare) and not (project_root / file).exists():
        return None, True, False  # rule 3
    home = str(Path.home()).lstrip("/")
    shaped = (
        bare.split("/", 1)[0] in _MACHINE_ISH
        or (bool(home) and bare.startswith(home + "/"))
        or _DRIVE.match(file) is not None
    )
    return file, False, shaped and not (project_root / file).exists()


class _Row(NamedTuple):
    """One row's repair: whether it was written, held, and the counts the write carried."""

    changed: bool = False
    held: bool = False
    ambiguous: int = 0
    dropped_absolute: int = 0
    dropped_temp: int = 0
    refreshed: bool = False


class _Plan(NamedTuple):
    kept: list[Anchor] | None  # None: nothing to write
    unsure_keys: list[str]
    dropped_absolute: int
    dropped_temp: int


def _plan(entry: MemoryEntry, project_root: Path) -> _Plan:
    """What to write for one row (``kept`` is ``None`` when unchanged) and what was dropped or left ambiguous."""
    kept: list[Anchor] = []
    dirty = False
    unsure_keys: list[str] = []
    dropped_absolute = dropped_temp = 0
    for anchor in entry.anchors:
        file, moved, unsure = _repaired_file(anchor.file, project_root)
        dirty = dirty or moved
        if unsure:
            unsure_keys.append(anchor.file)
        if file is None:
            dropped_absolute += anchor.file.startswith("/")
            dropped_temp += not anchor.file.startswith("/")
        else:
            kept.append(anchor.model_copy(update={"file": file}))
    return _Plan(kept if dirty else None, unsure_keys, dropped_absolute, dropped_temp)


def _repair_row(store: MemoryStore, entry: MemoryEntry, project_root: Path) -> _Row:
    """Write one row under ``if_revision``, rereading on a conflict; the anchor score is refreshed in the same patch."""
    from trw_memory.lifecycle.anchor_validation import compute_anchor_validity
    from trw_memory.lifecycle.correction import LearningPatch
    from trw_memory.storage._shared import revision_of

    current: MemoryEntry | None = entry
    ambiguous = 0
    for attempt in range(_CONFLICT_RETRIES):
        if current is None:
            return _Row(ambiguous=ambiguous)
        plan = _plan(current, project_root)
        if attempt == 0:
            ambiguous = len(plan.unsure_keys)
            if plan.unsure_keys:  # once per row: the id and keys are what an operator reviews
                logger.info("anchor_repair_ambiguous_key_kept", entry_id=current.id, files=plan.unsure_keys)
        if plan.kept is None:
            return _Row(ambiguous=ambiguous)
        patch = LearningPatch(
            anchors=plan.kept,
            # An empty list would score a perfect 1.0 (CORE-244 FR01 defect), and a patch cannot clear the
            # score to "never assessed", so a row that lost every anchor keeps the score it had.
            anchor_validity=compute_anchor_validity(plan.kept, project_root) if plan.kept else None,
            if_revision=revision_of(current),
        )
        result = store.correct(current.id, patch)
        status = result.get("status")
        if status == "updated":
            return _Row(True, False, ambiguous, plan.dropped_absolute, plan.dropped_temp, bool(plan.kept))
        if status != "conflict":
            logger.info("anchor_repair_row_skipped", entry_id=current.id, status=status)
            return _Row(held=True, ambiguous=ambiguous)
        current = store.get(current.id)
    logger.info("anchor_repair_row_skipped", entry_id=entry.id, status="conflict")
    return _Row(held=True, ambiguous=ambiguous)


def _read_cursor(path: Path) -> tuple[int, bool, str]:
    """``(offset, held, high-water ISO time)`` of the pass in progress; a fresh pass when unreadable or not v2.

    A cursor from an older repair version skipped rows today's rules would change, so it never resumes.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["version"] != _VERSION:
            raise ValueError("cursor from another repair version")
        return max(int(data["offset"]), 0), bool(data["held"]), str(data["hwm"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0, False, _now()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _since(entry: MemoryEntry, mark: datetime) -> bool:
    updated = entry.updated_at
    return (updated if updated.tzinfo else updated.replace(tzinfo=timezone.utc)) >= mark


@dataclass
class _Tally:
    """Running totals of one call; ambiguity is per row id, so a row re-scanned by a later pass counts once."""

    changed: int = 0
    dropped_absolute: int = 0
    dropped_temp: int = 0
    refreshed: int = 0
    held: bool = False
    ambiguous_by_id: dict[str, int] = field(default_factory=dict)

    def add(self, entry_id: str, row: _Row) -> None:
        self.changed += row.changed
        self.dropped_absolute += row.dropped_absolute
        self.dropped_temp += row.dropped_temp
        self.refreshed += row.refreshed
        self.held = self.held or row.held
        self.ambiguous_by_id[entry_id] = row.ambiguous


def _tail_is_clean(store: MemoryStore, namespace: str, project_root: Path, hwm: str, tally: _Tally) -> bool:
    """Repair every row updated since *hwm* (the top of the newest-first list); ``True`` when it settled.

    Repeats from the start of each scan until one changes nothing, so a row inserted while the
    tail was being repaired is caught too; ``False`` when that has not settled after a few passes.
    """
    for _ in range(_TAIL_PASSES):
        mark, started = datetime.fromisoformat(hwm), _now()
        limit = _PAGE
        while True:
            rows = store.list_entries(namespace, limit=limit)
            recent = [e for e in rows if _since(e, mark)]
            if len(rows) < limit or len(recent) < len(rows):
                break
            limit *= 2  # every listed row is newer than the mark: the tail may go deeper
        before = tally.changed
        for entry in recent:
            tally.add(entry.id, _repair_row(store, entry, project_root))
        if tally.held:
            return False
        if tally.changed == before:
            return True
        hwm = started
    return False


@dataclass(frozen=True)
class RepairOutcome:
    """What repair did: rows rewritten, anchors dropped or kept machine-shaped, and whether the marker was written.

    ``held`` means a row could not be written or the final scan did not settle: the marker stays absent.
    """

    changed: int = 0
    ambiguous: int = 0
    dropped_absolute: int = 0
    dropped_temp: int = 0
    validity_refreshed: int = 0
    held: bool = False
    complete: bool = False

    @property
    def review_note(self) -> str | None:
        """The operator-facing line for the deliver result, or ``None`` when nothing needs review."""
        if not self.ambiguous:
            return None
        return (
            f"{self.ambiguous} anchors look machine-shaped but aren't under this checkout; kept — review with "
            "the `anchor_repair_ambiguous_key_kept` log events (entry ids and keys)"
        )

    def __add__(self, other: RepairOutcome) -> RepairOutcome:
        """Counts add; ``held`` and ``complete`` are the later call's state."""
        return replace(
            other,
            changed=self.changed + other.changed,
            ambiguous=self.ambiguous + other.ambiguous,
            dropped_absolute=self.dropped_absolute + other.dropped_absolute,
            dropped_temp=self.dropped_temp + other.dropped_temp,
            validity_refreshed=self.validity_refreshed + other.validity_refreshed,
        )


def _marker_is_current(marker: Path) -> bool:
    try:  # a regular file only: a FIFO or device would block the read (codex r1 KI), and bad bytes are not v2
        return marker.is_file() and marker.read_bytes() == _MARKER_TEXT.encode("utf-8")
    except OSError:  # trw-fail-silent-allow: an unreadable marker is not a v2 marker, so the pass runs again
        return False


def repair_legacy_anchors(trw_dir: Path, project_root: Path) -> RepairOutcome:
    """Repair one page of the project namespace's machine-path anchors.

    Fail-open: a store that cannot be reached leaves the cursor and marker as they were.
    """
    marker = trw_dir / _MARKER
    if _marker_is_current(marker):
        return RepairOutcome(complete=True)
    from trw_mcp._checkout_write import write_checkout_file
    from trw_mcp.state._store_selection import selected_store

    store, namespace = selected_store(trw_dir)
    cursor_path = trw_dir / _CURSOR
    offset, held, hwm = _read_cursor(cursor_path)
    # The store API has no cursor: list newest-first up to this page's end and take the tail.
    page = store.list_entries(namespace, limit=offset + _PAGE)[offset:]
    tally = _Tally()
    for entry in page:
        tally.add(entry.id, _repair_row(store, entry, project_root))
    held = held or tally.held
    complete = False
    if len(page) >= _PAGE:
        state: dict[str, object] = {"version": _VERSION, "offset": offset + _PAGE, "held": held, "hwm": hwm}
    else:
        complete = not held and _tail_is_clean(store, namespace, project_root, hwm, tally)
        held = held or tally.held or not complete
        if complete:
            write_checkout_file(trw_dir, marker, _MARKER_TEXT)  # symlink-refusing checkout write
        # unclean: the next deliver starts a new pass
        state = {"version": _VERSION, "offset": 0, "held": False, "hwm": _now()}
    ambiguous_anchors = sum(tally.ambiguous_by_id.values())
    if tally.changed or ambiguous_anchors:
        logger.info(
            "anchor_repair_page",
            rows_changed=tally.changed,
            ambiguous_anchors=ambiguous_anchors,
            dropped_absolute=tally.dropped_absolute,
            dropped_temp=tally.dropped_temp,
        )
    write_checkout_file(trw_dir, cursor_path, json.dumps(state))
    return RepairOutcome(
        tally.changed,
        ambiguous_anchors,
        tally.dropped_absolute,
        tally.dropped_temp,
        tally.refreshed,  # rewritten rows that kept an anchor carried a recomputed score in the same patch
        held,
        complete,
    )


def repair_until_settled(trw_dir: Path, project_root: Path) -> RepairOutcome:
    """Repeat :func:`repair_legacy_anchors` until the marker is written or a pass holds; the summed outcome."""
    total = RepairOutcome()
    while not (total.complete or total.held):
        total += repair_legacy_anchors(trw_dir, project_root)
    return total
