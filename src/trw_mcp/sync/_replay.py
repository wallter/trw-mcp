"""``trw-mcp sync pull --full``: replay every team learning, resumably (PRD-CORE-280 FR02).

The periodic cycle pulls only what arrived after ``last_pull_seq``. A new or
migrated checkout, or one whose store was restored, needs everything, so this
walks the org's pages from sequence 0 through the same engine the cycle uses
(:func:`pull_and_merge`). It differs only in where it starts and when it stops:

* It holds the coordinator's sync lock, so it never races a cycle.
* Its position lives in a ``replay`` block in ``sync-state.json``. A crash or a
  held page leaves the block, and ``--resume`` continues from it. A second
  replay refuses to start over an unfinished one.
* Both cursors start at 0, the org cursor and the company cursor, so a restored
  store gets its company rows back too.
* It advances by the page's highest non-company ``sync_seq`` and stops on an
  empty page or one that moves neither the org nor the company cursor.
  ``last_pull_seq`` and ``last_company_pull_seq`` are only ever raised.
* A page the merge could not judge is retried three times, then the run stops
  with the block intact.
* It pushes nothing and leaves the ETag and the intel cache alone.

Every page appends one receipt line to ``<trw_dir>/sync-replay.jsonl``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from typing_extensions import TypedDict

from trw_mcp.sync._client_cycle import pull_and_merge

if TYPE_CHECKING:
    from trw_mcp.sync.client import BackendSyncClient
    from trw_mcp.sync.coordinator import SyncCoordinator

__all__ = ["PAGE_ATTEMPTS", "ReplayReport", "ReplayStatus", "run_full_pull"]

#: Tries per page before a held or failed page stops the run.
PAGE_ATTEMPTS = 3
_LOCK_POLL_SECONDS = 0.5


ReplayStatus = Literal["completed", "held", "paused", "locked", "unfinished"]


class ReplayReport(TypedDict):
    """How a full pull ended; anything but ``completed`` leaves the replay block for ``--resume``."""

    status: ReplayStatus
    pages: int
    pulled: int
    merged: int
    next_seq: int


@contextmanager
def _sync_lock(coordinator: SyncCoordinator, wait_seconds: float) -> Iterator[bool]:
    deadline = time.monotonic() + wait_seconds
    while True:
        with coordinator.acquire_sync_lock() as acquired:
            if acquired or time.monotonic() >= deadline:
                yield acquired
                return
        time.sleep(_LOCK_POLL_SECONDS)


def _receipt(path: Path, **fields: object) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), **fields}) + "\n")


async def run_full_pull(
    client: BackendSyncClient,
    *,
    resume: bool,
    max_pages: int,
    receipt_path: Path,
    wait_seconds: float = 0.0,
) -> ReplayReport:
    """Replay the org's team learnings from sequence 0, or from the unfinished block with *resume*.

    At most *max_pages* pages are pulled in this run; a run that reaches the
    bound reports ``paused`` and keeps the block.
    """
    coordinator = client._coordinator
    pulled = merged = 0

    def ended(status: ReplayStatus, pages: int, next_seq: int) -> ReplayReport:
        return {"status": status, "pages": pages, "pulled": pulled, "merged": merged, "next_seq": next_seq}

    with _sync_lock(coordinator, wait_seconds) as acquired:
        if not acquired:
            return ended("locked", 0, 0)
        block = coordinator.replay_state()
        if block is not None and not resume:
            return ended("unfinished", 0, int(str(block.get("next_seq", 0))))
        position = block or {}
        seq, company_seq, pages = (int(str(position.get(key, 0))) for key in ("next_seq", "company_seq", "pages"))
        coordinator.record_replay_page(next_seq=seq, company_seq=company_seq, pages=pages)
        for _ in range(max_pages):
            for _attempt in range(PAGE_ATTEMPTS):
                step = await pull_and_merge(client, pull_seq=seq, company_pull_seq=company_seq, etag=None)
                judged = step.result is not None and (step.cursor_may_advance or step.pulled == 0)
                if judged:
                    break
            next_company = max(company_seq, step.result.next_company_seq) if step.result is not None else company_seq
            moved = step.next_pull_seq > seq or next_company > company_seq
            outcome = "held" if not judged else "end" if step.pulled == 0 or not moved else "page"
            _receipt(
                receipt_path,
                page=pages + 1,
                since_seq=seq,
                next_seq=step.next_pull_seq,
                pulled=step.pulled,
                merged=step.merge.applied,
                outcome=outcome,
            )
            pulled, merged = pulled + step.pulled, merged + step.merge.applied
            if outcome == "held":
                return ended("held", pages, seq)
            if outcome == "end":
                coordinator.clear_replay()
                return ended("completed", pages, seq)
            seq, company_seq, pages = step.next_pull_seq, next_company, pages + 1
            coordinator.record_replay_page(next_seq=seq, company_seq=company_seq, pages=pages)
        return ended("paused", pages, seq)
