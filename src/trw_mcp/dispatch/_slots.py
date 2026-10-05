"""Per-user dispatch child slots (PRD-CORE-355-FR02..FR05).

Belongs to the ``trw_mcp.dispatch`` package. ``_runner.dispatch`` holds one slot
for the lifetime of the child it launches, so with
``dispatch_max_concurrent_children: N`` (N > 0) at most N dispatch children run
at once -- sync, fan-out lanes and background jobs alike (a background job's slot
is taken by its detached ``_run_job`` process, never by the server that spawned it).

Scope and semantics, stated because a bound is only as good as its fine print:

- **Per user, not per machine.** Slots are ``flock``-ed files
  ``~/.trw/runtime/dispatch-slots/slot-{0..N-1}.lock`` (``TRW_DISPATCH_SLOT_DIR``
  overrides the directory). Another OS user has another directory.
- **Largest active cap wins.** Participants with different caps share the same
  slot files, so a process with cap 4 can occupy slot-3 that a cap-2 process never
  looks at; the effective bound is the largest cap in use. A process whose cap is
  0 takes no slot and is uncounted.
- **No hold-and-wait (FR04).** A slot holder exports ``TRW_DISPATCH_SLOT_HELD=1``
  (and the slot directory) to its child. A dispatch that starts with that marker
  makes ONE non-blocking attempt: a free slot proceeds, none refuses at once with
  ``concurrency_cap``. A parent waiting on its child therefore never waits on a
  slot the parent itself holds, and the bound stays strict.
- **Lock order (FR05).** The slot is taken before the per-client credential lock
  (``_credentials.run_guarded`` runs inside it), never the reverse.
- **No inherited descriptors.** Slot fds are opened ``O_CLOEXEC`` and marked
  non-inheritable, and the runner launches with ``close_fds=True``, so a child (or
  an escaped grandchild) never keeps a slot past its owner. ``flock`` releases on
  process death.
- **The slot belongs to the ``_run_job`` process.** If the job watchdog kills ``_run_job`` while a
  detached child survives, the slot frees early and the cap can be exceeded in that edge.
- **Fail-open on an unusable slot directory.** An ``OSError`` creating or locking the slot files (EACCES,
  ENOSPC, read-only home) logs one ``dispatch_slot_unavailable`` warning, records ``outcome:
  "unavailable"`` in the policy event and runs the child unbounded.
- **Windows is unenforced.** ``_locking`` is a no-op there; the cap logs one
  warning and admits every dispatch.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import structlog

from trw_mcp._locking import _lock_ex_nb, _lock_un
from trw_mcp.dispatch._child_marker import dispatched_child_active
from trw_mcp.dispatch._runner_results import _early_result
from trw_mcp.dispatch._types import DispatchRequest, DispatchResult

__all__ = [
    "SLOT_DIR_ENV",
    "SLOT_HELD_ENV",
    "SlotGrant",
    "SlotSettings",
    "SlotUnavailableError",
    "dispatch_slot",
    "held_slot_env",
    "run_capped",
    "slot_dir",
    "slot_settings",
]

logger = structlog.get_logger(__name__)

#: Exported into a slot holder's child environment; its presence makes a nested dispatch non-blocking.
SLOT_HELD_ENV = "TRW_DISPATCH_SLOT_HELD"
#: Overrides the slot directory (tests, and a holder pinning its children to the same slot set).
SLOT_DIR_ENV = "TRW_DISPATCH_SLOT_DIR"
POLL_SECONDS = 0.5

try:
    import fcntl as _fcntl  # noqa: F401  # presence probe: advisory locks exist on this platform

    _ENFORCED = True
except ImportError:  # pragma: no cover - Windows
    _ENFORCED = False

_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_warned_unenforced = threading.Event()
#: The slot directory this thread's in-flight dispatch holds a slot in, else None.
_held_dir: contextvars.ContextVar[Path | None] = contextvars.ContextVar("trw_dispatch_slot_dir", default=None)


@dataclass(frozen=True)
class SlotSettings:
    """The operator's cap (0 = off) and top-level wait bound."""

    cap: int
    wait_s: float


def slot_settings() -> SlotSettings:
    """Read ``dispatch_max_concurrent_children`` / ``dispatch_slot_wait_s`` from the live config."""
    from trw_mcp.models.config import get_config  # config imports the dispatch types; import late

    cfg = get_config().dispatch
    return SlotSettings(cap=int(cfg.dispatch_max_concurrent_children), wait_s=float(cfg.dispatch_slot_wait_s))


@dataclass(frozen=True)
class SlotGrant:
    """A held slot: which one, how long it took, under which cap."""

    index: int | None
    cap: int
    wait_s: float
    nested: bool
    enforced: bool
    outcome: str = ""  # "unavailable" when the slot directory could not be used (dispatch fails open)

    def record(self) -> dict[str, object]:
        return {
            "outcome": self.outcome or ("acquired" if self.enforced else "unenforced"),
            "index": self.index,
            "cap": self.cap,
            "wait_s": round(self.wait_s, 3),
            "nested": self.nested,
        }


class SlotUnavailableError(Exception):
    """No slot freed within the bound (top level) or on the single attempt (nested)."""

    def __init__(self, cap: int, waited_s: float, *, nested: bool) -> None:
        self.cap, self.waited_s, self.nested = cap, waited_s, nested
        how = (
            f"a nested dispatch ({SLOT_HELD_ENV} set) found no free slot and does not wait"
            if nested
            else f"no slot freed within {waited_s:.1f}s (dispatch_slot_wait_s)"
        )
        super().__init__(
            f"concurrency_cap: all {cap} dispatch child slots are in use; {how}. "
            f"Raise dispatch_max_concurrent_children or retry later."
        )

    def record(self) -> dict[str, object]:
        return {
            "outcome": "refused",
            "index": None,
            "cap": self.cap,
            "wait_s": round(self.waited_s, 3),
            "nested": self.nested,
        }


def slot_dir() -> Path:
    """The per-user slot directory (``TRW_DISPATCH_SLOT_DIR`` when set)."""
    override = os.environ.get(SLOT_DIR_ENV)
    return Path(override) if override else Path.home() / ".trw" / "runtime" / "dispatch-slots"


def held_slot_env() -> dict[str, str]:
    """Env overlay for a child launched while this thread holds a slot; empty otherwise."""
    held = _held_dir.get()
    return {} if held is None else {SLOT_HELD_ENV: "1", SLOT_DIR_ENV: str(held)}


def _try_any(directory: Path, cap: int) -> tuple[int, int] | None:
    """Lock the first free ``slot-{i}.lock``; ``(index, fd)`` or None when all are held."""
    for index in range(cap):
        fd = os.open(directory / f"slot-{index}.lock", os.O_RDWR | os.O_CREAT | _O_CLOEXEC, 0o600)
        os.set_inheritable(fd, False)
        try:
            _lock_ex_nb(fd)
        except OSError:  # trw-fail-silent-allow: held by another dispatch; try the next slot
            os.close(fd)
            continue
        return index, fd
    return None


@contextlib.contextmanager
def dispatch_slot(
    cap: int, *, wait_s: float, nested: bool | None = None, directory: Path | None = None
) -> Iterator[SlotGrant]:
    """Hold one of *cap* slots for the body; :class:`SlotUnavailableError` when none frees in time.

    *nested* defaults to whether ``TRW_DISPATCH_SLOT_HELD`` is in this process's
    environment; a nested caller makes exactly one attempt and never sleeps.
    """
    if cap <= 0:
        raise ValueError("dispatch_slot needs cap > 0; cap 0 means the caller takes no slot")
    is_nested = (SLOT_HELD_ENV in os.environ) if nested is None else nested
    if not _ENFORCED:  # pragma: no cover - Windows
        if not _warned_unenforced.is_set():
            _warned_unenforced.set()
            logger.warning("dispatch_slot_cap_unenforced", reason="advisory file locks unavailable on this platform")
        yield SlotGrant(index=None, cap=cap, wait_s=0.0, nested=is_nested, enforced=False)
        return
    where = directory or slot_dir()
    start = time.monotonic()
    deadline = start + (0.0 if is_nested else max(0.0, wait_s))
    while True:
        try:
            where.mkdir(parents=True, exist_ok=True, mode=0o700)
            got = _try_any(where, cap)
        except OSError as exc:  # EACCES / ENOSPC / read-only home: the cap is a safety rail, not a gate
            logger.warning("dispatch_slot_unavailable", directory=str(where), error=str(exc))
            yield SlotGrant(index=None, cap=cap, wait_s=0.0, nested=is_nested, enforced=False, outcome="unavailable")
            return
        now = time.monotonic()
        if got is not None:
            break
        if is_nested or now >= deadline:
            raise SlotUnavailableError(cap, now - start, nested=is_nested)
        time.sleep(min(POLL_SECONDS, deadline - now))
    index, fd = got
    token = _held_dir.set(where)
    try:
        yield SlotGrant(index=index, cap=cap, wait_s=now - start, nested=is_nested, enforced=True)
    finally:
        _held_dir.reset(token)
        try:
            _lock_un(fd)
        finally:
            os.close(fd)


def run_capped(
    req: DispatchRequest, run: Callable[[], DispatchResult], *, background: bool, max_wait_s: float | None = None
) -> DispatchResult:
    """*run* while holding one child slot; a ``concurrency_cap`` refusal (no child launched) when none frees.

    Cap 0 calls *run* directly and touches no file (NFR01). A *background* job (``_run_job``) waits at
    most half its timeout, so slot wait + run stays inside the job watchdog (prelaunch + 1.5x timeout).
    *max_wait_s* (when given) further caps the wait, so a synchronous MCP caller gets ``concurrency_cap``
    back quickly instead of holding its request thread. A dispatched child's server never queues.
    """
    settings = slot_settings()
    if settings.cap <= 0 or dispatched_child_active():
        return run()
    wait_s = min(settings.wait_s, req.timeout_s / 2) if background else settings.wait_s
    if max_wait_s is not None:
        wait_s = min(wait_s, max_wait_s)
    from trw_mcp.dispatch._usage import record_slot_outcome  # _usage -> _policy -> config -> this package

    try:
        with dispatch_slot(settings.cap, wait_s=wait_s) as grant:
            result = run()
    except SlotUnavailableError as exc:
        logger.warning("dispatch_slot_refused", client=req.client, **exc.record())
        record_slot_outcome(req.client, exc.record())
        return _early_result(req, [], exit_code=-1, stderr=str(exc), silence_reason="concurrency_cap")
    logger.info("dispatch_slot_released", client=req.client, **grant.record())
    record_slot_outcome(req.client, grant.record())
    return result
