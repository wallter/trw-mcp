"""One-time repair of anchors stored with a machine path (E2E-INC-025).

Belongs to the deliver-time maintenance in ``tools/_ceremony_deliver_steps.py``.

Before the fix, ``trw_learn`` persisted an anchor as the ABSOLUTE path of the
edited file with its leading ``/`` stripped (``Users/<name>/proj/src/a.py``).
That key never matched the repo-relative one the pre-edit hint looks up, and it
carried the machine's directory layout (including ``$HOME``) into a row that can
sync off the machine. Matching alone would leave that path stored, so rows are
repaired. Only two rules may ever change a key, and neither looks at whether a
file exists (a stale anchor to a deleted source is normal and stays):

1. REWRITE: the key starts with this checkout's root (resolved or not, leading
   ``/`` stripped); it becomes repo-relative.
2. DROP: the key's own shape proves an absolute origin: it starts with ``/``.
   Nothing else is provable: ``C:/src/a.py`` is a valid relative POSIX path, so a
   drive-letter shape is kept and counted as ambiguous (codex r3), never dropped.

Everything else is KEPT, always. A spelling that merely resembles a machine path
(``Users/<x>/...``, ``home/<x>/...``, ``tmp/...``, the home directory minus its
slash) is not provenance: ``srv/alice/x.py`` is a valid relative key on a machine
whose home is ``/srv/alice``. Such a key that is machine-shaped and absent under
the root is logged and counted as ambiguous. Ambiguity does NOT hold the marker:
the classification is stable, so rescanning could never resolve it, and holding
would rescan every deliver forever. The count reaches the operator in the deliver
result (``RepairOutcome.review_note``). New writes are already repo-relative, so
this set can only shrink over time.

The marker ``.trw/context/anchors_repo_relative`` is written after a COMPLETE,
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


def _repaired_file(file: str, project_root: Path) -> tuple[str | None, bool, bool]:
    """``(new file or None to drop, changed, ambiguous)`` for one stored anchor ``file``."""
    bare = file.lstrip("/")
    for root in dict.fromkeys((project_root, project_root.resolve())):
        prefix = str(root).lstrip("/") + "/"
        if bare.startswith(prefix) and len(bare) > len(prefix):
            return bare[len(prefix) :], True, False  # rule 1
    if file.startswith("/"):
        return None, True, False  # rule 2: the only shape no relative POSIX path can have
    home = str(Path.home()).lstrip("/")
    shaped = (
        bare.split("/", 1)[0] in _MACHINE_ISH
        or (bool(home) and bare.startswith(home + "/"))
        or _DRIVE.match(file) is not None
    )
    return file, False, shaped and not (project_root / file).exists()


def _plan(entry: MemoryEntry, project_root: Path) -> tuple[list[Anchor] | None, list[str]]:
    """``(anchors to write or None when unchanged, the ambiguous keys kept)`` for one row."""
    kept: list[Anchor] = []
    dirty = False
    unsure_keys: list[str] = []
    for anchor in entry.anchors:
        file, moved, unsure = _repaired_file(anchor.file, project_root)
        dirty = dirty or moved
        if unsure:
            unsure_keys.append(anchor.file)
        if file is not None:
            kept.append(anchor.model_copy(update={"file": file}))
    return (kept if dirty else None), unsure_keys


def _repair_row(store: MemoryStore, entry: MemoryEntry, project_root: Path) -> tuple[bool, bool, int]:
    """``(changed, held, ambiguous anchors)``: write one row under ``if_revision``, rereading on a conflict."""
    from trw_memory.lifecycle.correction import LearningPatch
    from trw_memory.storage._shared import revision_of

    current: MemoryEntry | None = entry
    ambiguous = 0
    for attempt in range(_CONFLICT_RETRIES):
        if current is None:
            return False, False, ambiguous
        kept, unsure_keys = _plan(current, project_root)
        if attempt == 0:
            ambiguous = len(unsure_keys)
            if unsure_keys:  # once per row: the id and keys are what an operator reviews
                logger.info("anchor_repair_ambiguous_key_kept", entry_id=current.id, files=unsure_keys)
        if kept is None:
            return False, False, ambiguous
        result = store.correct(current.id, LearningPatch(anchors=kept, if_revision=revision_of(current)))
        status = result.get("status")
        if status == "updated":
            return True, False, ambiguous
        if status != "conflict":
            logger.info("anchor_repair_row_skipped", entry_id=current.id, status=status)
            return False, True, ambiguous
        current = store.get(current.id)
    logger.info("anchor_repair_row_skipped", entry_id=entry.id, status="conflict")
    return False, True, ambiguous


def _read_cursor(path: Path) -> tuple[int, bool, str]:
    """``(offset, held, high-water ISO time)`` of the pass in progress; a fresh pass when unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return max(int(data["offset"]), 0), bool(data["held"]), str(data["hwm"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0, False, _now()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _since(entry: MemoryEntry, mark: datetime) -> bool:
    updated = entry.updated_at
    return (updated if updated.tzinfo else updated.replace(tzinfo=timezone.utc)) >= mark


def _tail_is_clean(
    store: MemoryStore, namespace: str, project_root: Path, hwm: str, ambiguous_by_id: dict[str, int]
) -> tuple[int, bool]:
    """Repair every row updated since *hwm* (the top of the newest-first list); ``(rows changed, clean)``.

    Repeats from the start of each scan until one changes nothing, so a row inserted while the
    tail was being repaired is caught too; ``False`` when that has not settled after a few passes.
    """
    total = 0
    for _ in range(_TAIL_PASSES):
        mark, started = datetime.fromisoformat(hwm), _now()
        limit = _PAGE
        while True:
            rows = store.list_entries(namespace, limit=limit)
            recent = [e for e in rows if _since(e, mark)]
            if len(rows) < limit or len(recent) < len(rows):
                break
            limit *= 2  # every listed row is newer than the mark: the tail may go deeper
        changed, held = 0, False
        for entry in recent:
            did, was_held, ambiguous = _repair_row(store, entry, project_root)
            changed, held = changed + did, held or was_held
            ambiguous_by_id[entry.id] = ambiguous  # a row re-scanned by a later pass is counted once
        total += changed
        if held:
            return total, False
        if not changed:
            return total, True
        hwm = started
    return total, False


class RepairOutcome(NamedTuple):
    """What one deliver's repair did: rows rewritten, and anchors kept that look machine-shaped."""

    changed: int
    ambiguous: int

    @property
    def review_note(self) -> str | None:
        """The operator-facing line for the deliver result, or ``None`` when nothing needs review."""
        if not self.ambiguous:
            return None
        return (
            f"{self.ambiguous} anchors look machine-shaped but aren't under this checkout; kept — review with "
            "the `anchor_repair_ambiguous_key_kept` log events (entry ids and keys)"
        )


def repair_legacy_anchors(trw_dir: Path, project_root: Path) -> RepairOutcome:
    """Repair one page of the project namespace's machine-path anchors.

    Fail-open: a store that cannot be reached leaves the cursor and marker as they were.
    """
    marker = trw_dir / _MARKER
    if marker.exists():
        return RepairOutcome(0, 0)
    from trw_mcp._checkout_write import write_checkout_file
    from trw_mcp.state._store_selection import selected_store

    store, namespace = selected_store(trw_dir)
    cursor_path = trw_dir / _CURSOR
    offset, held, hwm = _read_cursor(cursor_path)
    # The store API has no cursor: list newest-first up to this page's end and take the tail.
    page = store.list_entries(namespace, limit=offset + _PAGE)[offset:]
    changed = 0
    ambiguous_by_id: dict[str, int] = {}
    for entry in page:
        did, was_held, ambiguous = _repair_row(store, entry, project_root)
        changed += did
        held = held or was_held
        ambiguous_by_id[entry.id] = ambiguous
    if len(page) >= _PAGE:
        state: dict[str, object] = {"offset": offset + _PAGE, "held": held, "hwm": hwm}
    else:
        clean = not held
        if clean:
            more, clean = _tail_is_clean(store, namespace, project_root, hwm, ambiguous_by_id)
            changed += more
        if clean:
            write_checkout_file(trw_dir, marker, "done\n")  # symlink-refusing checkout write
        state = {"offset": 0, "held": False, "hwm": _now()}  # unclean: the next deliver starts a new pass
    ambiguous_anchors = sum(ambiguous_by_id.values())
    if changed or ambiguous_anchors:
        logger.info("anchor_repair_page", rows_changed=changed, ambiguous_anchors=ambiguous_anchors)
    write_checkout_file(trw_dir, cursor_path, json.dumps(state))
    return RepairOutcome(changed, ambiguous_anchors)
