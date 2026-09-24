"""Combined TRW post-commit maintenance entry point (PRD-CORE-231 FR01/FR02).

A commit changes two things at once that TRW cares about:

* the HEAD sha, which invalidates the whole sha-keyed T2 hint sidecar (FR01);
* the working tree, which can make a learning's assertions or anchors go stale
  (FR02) — and staleness is otherwise only noticed if someone happens to recall
  that exact entry.

Both are therefore driven from ONE git ``post-commit`` hook.

Single-flight (PRD-INFRA-186)
-----------------------------
The FR02 sweep pages through EVERY entry in the store, and the hook fires on
every commit. Measured on 2026-09-16 with ``ps``/``lsof``/``sample``
(sub_lynArGCloVDuWffm): 24 commits in three hours produced **27 concurrent
workers**, elapsed up to 4h50m, ~0.95 of a core, each holding ``memory.db`` open
— and each registering as a store writer, which pushed ``trw_learn`` into
``writer_pressure`` deferral at ``writer_count=30`` against a threshold of 8.
The framework's own maintenance hook was defeating the bounded-deferral work it
depends on.

So this module is now a controller around those two steps:

* **one worker at a time per store**, via an ``O_CREAT | O_EXCL`` lock held in the
  trw directory the sweep will actually open (not merely the repository — they
  can differ, and locking the wrong one leaves two workers on one database);
* **crash recovery**, by recording the owning PID and reclaiming a lock whose
  owner is gone. A LIVE owner is never evicted on age: the owner's own budget is
  what bounds how long it may hold the lock;
* **coalescing without loss**, via a pending marker. An arrival that cannot get
  the lock records itself; the owner consumes the marker and runs exactly ONE
  follow-up pass. Arrivals during that follow-up leave the marker for the next
  commit rather than extending the run, so passes per acquisition are bounded at
  two;
* **a wall-clock budget** (``TRW_POST_COMMIT_BUDGET_SECONDS``, default 300s)
  raised through ``SIGALRM``.

Fail-open throughout: a git hook must never fail or stall ``git commit``. Every
step is individually guarded and the lock is released in a ``finally``.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = structlog.get_logger(__name__)

#: Written after every hook invocation that actually SWEEPS. Operators (and the
#: wiring test) use it to prove the hook is installed and firing, which is
#: precisely the "delivered != wired" failure this file exists to close. A
#: DEFERRED run deliberately does not write it — overwriting the running owner's
#: receipt with a no-op record would erase the only account of the real sweep.
RECEIPT_REL_PATH = Path(".trw") / "runtime" / "post-commit-receipt.json"

#: Single-flight lock and pending marker, relative to the RESOLVED trw dir.
LOCK_REL_PATH = Path("runtime") / "post-commit.lock"
PENDING_REL_PATH = Path("runtime") / "post-commit-pending.json"

#: Env var the bundled hook exports so a worker that starts after HEAD has moved
#: again still attributes its run to the commit that summoned it.
HEAD_ENV_VAR = "TRW_POST_COMMIT_HEAD"
BUDGET_ENV_VAR = "TRW_POST_COMMIT_BUDGET_SECONDS"
_DEFAULT_BUDGET_SECONDS = 300.0

#: A lock file whose record cannot be read is reclaimed only once it is older
#: than this. The gap between ``O_CREAT | O_EXCL`` and the record write is a
#: single ``write``; without a grace period a second arrival could read that
#: momentary empty file as abandoned and both would own the lock.
_UNREADABLE_LOCK_GRACE_SECONDS = 30.0


class _SweepDeadline(BaseException):
    """Raised by the budget timer to stop an over-running sweep.

    Derives from ``BaseException``, NOT ``Exception``, and that is load-bearing:
    trw-memory's ``run_maintain_verify`` catches ``Exception`` per entry and the
    verification pass catches it again, so an ``Exception``-derived deadline would be
    swallowed by the first guard it met while the one-shot timer stayed
    exhausted — the budget would silently not exist.
    """


@dataclass(slots=True)
class PostCommitReceipt:
    """Observable record of one post-commit maintenance run."""

    ran_at: str = ""
    head_sha: str = ""
    #: Targets a sidecar on disk demonstrably describes after the refresh —
    #: NOT the number of files the refresh was asked to cover, and not a count
    #: of exit codes. It used to be ``len(plan.files)``, so a 13-file commit
    #: that left one usable artifact reported ``sidecar_files: 13``.
    sidecar_files: int = 0
    #: What the refresh was asked to cover. Kept beside the achieved count so
    #: the shortfall is legible in the receipt instead of requiring a log dig:
    #: ``13 planned / 1 refreshed`` is a fact an operator can act on.
    sidecar_files_planned: int = 0
    sidecar_skipped_reason: str = ""
    verify_entries_processed: int = 0
    verify_stale_transitions: int = 0
    verify_cleared_transitions: int = 0
    #: ``acquired`` | ``reclaimed`` | ``deferred`` | ``unlocked``. ``unlocked``
    #: means the lock could not be created at all (unwritable runtime dir) and
    #: the sweep ran anyway — fail-open, but NOT single-flight, and the receipt
    #: says which of the two happened rather than implying the guarantee.
    lock_state: str = ""
    #: This run could not sweep and left a marker for the running owner.
    pending_marked: bool = False
    #: This run consumed a marker and ran a second pass for it.
    follow_up_ran: bool = False
    #: The budget fired and the sweep was cut short. The per-entry verdicts
    #: already persisted are kept; the corpus tail was NOT reached.
    bounded_stop: bool = False
    duration_ms: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Plain mapping for the JSON receipt."""
        return {
            "ran_at": self.ran_at,
            "head_sha": self.head_sha,
            "sidecar_files": self.sidecar_files,
            "sidecar_files_planned": self.sidecar_files_planned,
            "sidecar_skipped_reason": self.sidecar_skipped_reason,
            "verify_entries_processed": self.verify_entries_processed,
            "verify_stale_transitions": self.verify_stale_transitions,
            "verify_cleared_transitions": self.verify_cleared_transitions,
            "lock_state": self.lock_state,
            "pending_marked": self.pending_marked,
            "follow_up_ran": self.follow_up_ran,
            "bounded_stop": self.bounded_stop,
            "duration_ms": self.duration_ms,
            "errors": self.errors,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _head_sha(repo_root: Path) -> str:
    """The commit this run is for: the hook's value first, then git."""
    passed = os.environ.get(HEAD_ENV_VAR, "").strip()
    if passed:
        return passed
    try:
        from trw_mcp.tools._sidecar_substrate import resolve_git_sha

        return str(resolve_git_sha(repo_root) or "")
    except Exception:  # justified: fail-open, the sha is diagnostic only
        logger.debug("post_commit_head_sha_unresolved", exc_info=True)
        return ""


def _sweep_trw_dir(repo_root: Path) -> Path:
    """The trw directory the FR02 sweep will actually open.

    ``run_maintain_verify_for_project`` resolves its backend through
    ``resolve_trw_dir()``, which honours ``TRW_PROJECT_ROOT`` and the configured
    ``trw_dir``. Locking ``repo_root/.trw`` while sweeping somewhere else would
    put two workers on one database, so the lock follows the store.
    """
    try:
        from trw_mcp.state._paths import resolve_trw_dir

        return resolve_trw_dir()
    except Exception:  # justified: fail-open, the repo-local .trw is the documented default
        logger.debug("post_commit_trw_dir_unresolved", exc_info=True)
        return repo_root / ".trw"


def _owner_is_alive(pid: int) -> bool:
    """Is *pid* a running process on THIS host?

    ``PermissionError`` means the PID exists and belongs to another user, which
    is alive. Only ``ProcessLookupError`` proves absence. PID identity is local:
    this lock is specified for a local filesystem on a single host, which is
    what a git hook is.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:  # trw-fail-silent-allow: False IS the answer to "is this PID running" -- an absent process is the finding, not a swallowed failure
        return False
    except OSError:
        return True
    return True


def _lock_is_abandoned(lock_path: Path) -> bool:
    """True when the next arrival may take over an existing lock."""
    try:
        record = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = None
    if isinstance(record, dict) and isinstance(record.get("pid"), int):
        return not _owner_is_alive(int(record["pid"]))
    # No readable owner. That is either a crash mid-write or the millisecond
    # between create and write, and only age tells them apart.
    try:
        age = time.time() - lock_path.stat().st_mtime
    except OSError:  # trw-fail-silent-allow: fail CLOSED -- a lock we cannot stat is not a lock we have proven abandoned, and stealing it would re-create the concurrent-sweep defect
        return False
    return age > _UNREADABLE_LOCK_GRACE_SECONDS


def _acquire_lock(lock_path: Path, head_sha: str) -> str | None:
    """Take the single-flight lock; ``None`` when a live owner holds it.

    Returns ``"acquired"``, ``"reclaimed"``, or ``"unlocked"`` (the lock could
    not be created, so the caller runs WITHOUT the guarantee rather than skipping
    maintenance entirely).
    """
    state = "acquired"
    for _attempt in (1, 2):
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if not _lock_is_abandoned(lock_path):
                return None
            with contextlib.suppress(OSError):
                lock_path.unlink()
            state = "reclaimed"
            continue
        except OSError:
            logger.warning("post_commit_lock_unavailable", lock=str(lock_path), exc_info=True)
            return "unlocked"
        # Publish ownership through the SAME descriptor the exclusive create
        # returned, so the empty window is one write wide.
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "started_at": _now_iso(), "head_sha": head_sha}, stream)
        return state
    return None


def _release_lock(lock_path: Path) -> None:
    """Delete the lock only while it still names this process."""
    try:
        record = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = None
    if isinstance(record, dict) and record.get("pid") != os.getpid():
        # Someone reclaimed it; deleting now would strip THEIR lock.
        logger.warning("post_commit_lock_reclaimed_by_another_owner", lock=str(lock_path))
        return
    with contextlib.suppress(OSError):
        lock_path.unlink()


def _mark_pending(pending_path: Path, head_sha: str) -> bool:
    """Record that this commit arrived while a sweep was already running."""
    try:
        pending_path.parent.mkdir(parents=True, exist_ok=True)
        pending_path.write_text(
            json.dumps({"head_sha": head_sha, "marked_at": _now_iso()}),
            encoding="utf-8",
        )
    except OSError:  # trw-fail-silent-allow: the WARNING is the durable record and the returned False is reported in the receipt as pending_marked, so an unwritable marker is visible rather than assumed
        logger.warning("post_commit_pending_marker_unwritable", marker=str(pending_path), exc_info=True)
        return False
    return True


def _consume_pending(pending_path: Path) -> bool:
    """Take the pending marker if present; ``True`` when one was consumed."""
    try:
        existed = pending_path.is_file()
        if existed:
            pending_path.unlink()
    except OSError:  # trw-fail-silent-allow: an unreadable marker is treated as absent so the sweep still runs (fail-open into a git hook); the WARNING is the durable record
        logger.warning("post_commit_pending_marker_unreadable", marker=str(pending_path), exc_info=True)
        return False
    return existed


def _budget_seconds() -> float:
    """The wall-clock ceiling for one worker; the default on anything invalid."""
    raw = os.environ.get(BUDGET_ENV_VAR, "").strip()
    if not raw:
        return _DEFAULT_BUDGET_SECONDS
    try:
        parsed = float(raw)
    except ValueError:
        logger.warning("post_commit_budget_unparseable", value=raw)
        return _DEFAULT_BUDGET_SECONDS
    if parsed <= 0 or parsed != parsed or parsed == float("inf"):
        logger.warning("post_commit_budget_out_of_range", value=raw)
        return _DEFAULT_BUDGET_SECONDS
    return parsed


@contextlib.contextmanager
def _deadline(seconds: float) -> Iterator[bool]:
    """Arm the budget; yields ``True`` when a HARD stop is in place.

    Yields ``False`` — boundary-only bounding, enforced by the caller between
    passes — when ``SIGALRM`` is unavailable, when this is not the main thread,
    or when a timer is ALREADY armed. That last case is deliberate: restoring a
    saved relative timer afterwards would postpone its original deadline, so an
    existing timer is left strictly alone rather than quietly rescheduled.
    """
    if not hasattr(signal, "setitimer") or threading.current_thread() is not threading.main_thread():
        yield False
        return
    try:
        already_armed = signal.getitimer(signal.ITIMER_REAL)[0]
    except (OSError, ValueError):  # pragma: no cover
        yield False
        # trw-fail-silent-allow: False is the honest "no hard stop armed" report; the caller bounds at pass boundaries
        return
    if already_armed:
        yield False
        return

    def _fire(_signum: int, _frame: object) -> None:
        raise _SweepDeadline

    previous = signal.signal(signal.SIGALRM, _fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield True
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _run_pass(repo_root: Path, source_env: dict[str, str] | None, receipt: PostCommitReceipt) -> None:
    """One maintenance pass: FR01's sidecar refresh, then FR02's verify sweep.

    The receipt carries the LAST pass's counts (a follow-up re-sweeps the same
    corpus, so summing would double-count), with ``follow_up_ran`` saying that a
    second pass happened. Errors accumulate across both.
    """
    try:
        from trw_mcp.tools._hint_sidecar_refresh import run_post_commit_refresh

        outcome = run_post_commit_refresh(repo_root, dict(source_env if source_env is not None else os.environ))
        receipt.sidecar_files = outcome.refreshed_files
        receipt.sidecar_files_planned = len(outcome.plan.files)
        receipt.sidecar_skipped_reason = outcome.plan.skipped_reason
    except Exception as exc:  # justified: fail-open, must never block git commit
        logger.debug("post_commit_sidecar_refresh_failed", exc_info=True)
        receipt.errors.append(f"sidecar_refresh: {exc}")

    try:
        from trw_mcp.tools._maintain_verify import run_maintain_verify_for_project

        summary = run_maintain_verify_for_project()
        receipt.verify_entries_processed = summary.entries_processed
        receipt.verify_stale_transitions = summary.stale_transitions
        receipt.verify_cleared_transitions = summary.cleared_transitions
    except Exception as exc:  # justified: fail-open, must never block git commit
        logger.debug("post_commit_maintain_verify_failed", exc_info=True)
        receipt.errors.append(f"maintain_verify: {exc}")


def run_post_commit(repo_root: Path, source_env: dict[str, str] | None = None) -> PostCommitReceipt:
    """Run post-commit maintenance under single-flight, and write the receipt.

    Args:
        repo_root: Repository the commit landed in.
        source_env: Environment to project onto the subprocess allowlist;
            defaults to the current process environment.

    Returns:
        A :class:`PostCommitReceipt`. Never raises.
    """
    started = time.monotonic()
    head_sha = _head_sha(repo_root)
    receipt = PostCommitReceipt(ran_at=_now_iso(), head_sha=head_sha)

    trw_dir = _sweep_trw_dir(repo_root)
    lock_path = trw_dir / LOCK_REL_PATH
    pending_path = trw_dir / PENDING_REL_PATH

    lock_state = _acquire_lock(lock_path, head_sha)
    if lock_state is None:
        receipt.lock_state = "deferred"
        receipt.pending_marked = _mark_pending(pending_path, head_sha)
        receipt.duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "post_commit_deferred_to_running_sweep",
            head_sha=head_sha,
            lock=str(lock_path),
            pending_marked=receipt.pending_marked,
        )
        return receipt

    receipt.lock_state = lock_state
    budget = _budget_seconds()
    try:
        with _deadline(budget) as hard_stop_armed:
            try:
                # A marker left by an earlier run is this run's work too.
                _consume_pending(pending_path)
                _run_pass(repo_root, source_env, receipt)
                # Exactly one follow-up, and only inside the budget.
                if _consume_pending(pending_path) and (time.monotonic() - started) < budget:
                    receipt.follow_up_ran = True
                    _run_pass(repo_root, source_env, receipt)
            except _SweepDeadline:
                receipt.bounded_stop = True
                logger.warning(
                    "post_commit_sweep_bounded",
                    head_sha=head_sha,
                    budget_seconds=budget,
                    detail=(
                        "the sweep was cut short at its budget; verdicts already persisted are kept, "
                        "but run_maintain_verify restarts its cursor at the head of the corpus, so the "
                        "tail was not reached -- the full-corpus guarantee is the unbudgeted nightly sweep"
                    ),
                )
        receipt.duration_ms = int((time.monotonic() - started) * 1000)
        _write_receipt(repo_root, receipt)
        logger.info(
            "post_commit_maintenance_complete",
            head_sha=receipt.head_sha,
            lock_state=receipt.lock_state,
            follow_up_ran=receipt.follow_up_ran,
            bounded_stop=receipt.bounded_stop,
            hard_stop_armed=hard_stop_armed,
            sidecar_files=receipt.sidecar_files,
            sidecar_files_planned=receipt.sidecar_files_planned,
            verify_entries_processed=receipt.verify_entries_processed,
            duration_ms=receipt.duration_ms,
            errors=len(receipt.errors),
        )
    finally:
        _release_lock(lock_path)
    return receipt


def _write_receipt(repo_root: Path, receipt: PostCommitReceipt) -> None:
    """Persist the receipt; a write failure is itself non-fatal."""
    try:
        path = repo_root / RECEIPT_REL_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt.as_dict(), indent=2), encoding="utf-8")
    except OSError:
        logger.debug("post_commit_receipt_write_failed", exc_info=True)


def read_receipt(repo_root: Path) -> dict[str, Any] | None:
    """Return the last post-commit receipt, or ``None`` when absent/unreadable."""
    try:
        raw = (repo_root / RECEIPT_REL_PATH).read_text(encoding="utf-8")
        parsed: Any = json.loads(raw)
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


__all__ = [
    "BUDGET_ENV_VAR",
    "HEAD_ENV_VAR",
    "LOCK_REL_PATH",
    "PENDING_REL_PATH",
    "RECEIPT_REL_PATH",
    "PostCommitReceipt",
    "read_receipt",
    "run_post_commit",
]
