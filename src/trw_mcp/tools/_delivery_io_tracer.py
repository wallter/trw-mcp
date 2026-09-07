"""Durable input/output tracer for the FR03 census gate — PRD-FIX-127 FR05.

PRD-CORE-208 FR03 required "a runtime tracer [that] SHALL instrument
FileStateWriter writes/appends, FileEventLogger appends, SQLite commits ...".
What shipped instead read the delivery journal's OWN step rows and compared them
to the hand-written list the wiring wrote them from, so the census gate could only
fail if somebody DELETED a ``step()`` call — never if somebody ADDED an unjournaled
durable write, which is the failure the gate advertises. This module closes that.

It observes three local durable seams for the duration of one deliver and
attributes each observation to the crash boundary that was open when it happened:

- the four mutating methods of :class:`~trw_mcp.state.persistence.FileStateWriter`;
- :meth:`~trw_mcp.state.persistence.FileEventLogger.log_event`;
- ``commit`` on every SQLite connection opened inside the traced window.

Network sends (``D07`` learning fan-out, ``D14`` telemetry batch) are deliberately
out of scope: both are ``non_replayable`` and their registered proof is
receiver-side, so a local tracer cannot observe them honestly.

**Test scope.** Nothing here runs in a production deliver: the instrumentation is
installed by :func:`trace_durable_writes` and fully removed when it exits, and no
production module imports this one (NFR03).
"""

from __future__ import annotations

import sqlite3
import threading
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from trw_mcp._delivery_boundary import open_boundaries
from trw_mcp.tools._delivery_effect_registry import DELIVERY_EFFECT_REGISTRY, unjournaled_effect_ids

#: Substrings of a write target that are journal MACHINERY rather than a delivery
#: effect. The delivery operation store is written by ``begin_step`` itself, so
#: attributing its commits to a boundary would be circular.
JOURNAL_INTERNAL_TARGETS: tuple[str, ...] = ("delivery/operations.sqlite3",)

#: Frames that are the tracer's own plumbing or the writer implementation, never
#: the caller we want to name.
_SELF_MODULES: tuple[str, ...] = ("_delivery_io_tracer.py", "state/persistence.py", "_persistence_helpers.py")

#: Path fragment identifying the structlog JSONL file sink. Those appends ARE
#: census effect ``S21`` (structured application log emissions), whose declared
#: boundary is ``unjournaled``. Matched on TARGET rather than caller: structlog's
#: sink write is not guaranteed to happen synchronously on ``log_deliver_complete``'s
#: own stack frame (buffering/handler dispatch), so a caller-chain match alone
#: would be unreliable for this one effect.
APPLICATION_LOG_MARKER = "/logs/"

#: First-party callers that write durably inside a deliver but are not delivery
#: effects at all, so no descriptor can honestly own them. Kept deliberately tiny
#: and named: every addition here is a claim that the write is not delivery
#: evidence, which is exactly the claim FR05 exists to stop people making silently.
NON_DELIVERY_CALLERS: frozenset[str] = frozenset(
    {
        # The log_tool_call decorator's own per-invocation audit. It wraps EVERY
        # tool, so it is harness machinery around the deliver, not a deliver effect.
        "_write_tool_event",
        # Recall access bookkeeping the nudge engine commits while resolving pool
        # content. It is a read path shared by every tool; the mutation belongs to
        # the recall subsystem, not to delivery, so no descriptor can honestly own
        # it. Reached from _attach_deliver_ceremony_status during a deliver.
        "_try_learning_nudge_content",
    }
)


@dataclass(frozen=True)
class DurableWrite:
    """One observed durable mutation and the boundary that was open for it."""

    seam: str
    target: str
    boundary: str
    callers: tuple[str, ...]

    @property
    def attributed(self) -> bool:
        """True iff a crash boundary was open when this write happened."""
        return bool(self.boundary)

    def describe(self) -> str:
        return f"{self.seam}({self.target}) via {' -> '.join(self.callers[-4:]) or '<unknown>'}"


@dataclass
class DurableWriteTrace:
    """Thread-safe collector for one traced deliver."""

    observations: list[DurableWrite] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, seam: str, target: str) -> None:
        if any(marker in target for marker in JOURNAL_INTERNAL_TARGETS):
            return
        boundaries = open_boundaries()
        with self._lock:
            self.observations.append(
                DurableWrite(
                    seam=seam,
                    target=target,
                    boundary=boundaries[-1] if boundaries else "",
                    callers=_caller_chain(),
                )
            )

    def unattributed(self) -> tuple[DurableWrite, ...]:
        """Every observed write that happened with NO crash boundary open."""
        with self._lock:
            return tuple(obs for obs in self.observations if not obs.attributed)

    def unexplained(self) -> tuple[DurableWrite, ...]:
        """Writes with no open boundary AND no legitimate exemption (the FR03 gate).

        A clean deliver returns ``()``. Anything here is a durable mutation that no
        registered descriptor accounts for — the exact failure the previous
        journal-reading gate could never detect, because it only ever read back the
        ids the wiring itself had written.
        """
        return tuple(obs for obs in self.unattributed() if _exemption(obs) is None)

    def by_boundary(self) -> frozenset[str]:
        """The set of effect ids that were credited with at least one real write."""
        with self._lock:
            return frozenset(obs.boundary for obs in self.observations if obs.attributed)


def _caller_chain() -> tuple[str, ...]:
    """First-party function names on the stack, outermost first."""
    chain: list[str] = []
    for frame in traceback.extract_stack()[:-3]:
        if "trw_mcp" not in frame.filename:
            continue
        if any(marker in frame.filename for marker in _SELF_MODULES):
            continue
        chain.append(frame.name)
    return tuple(chain)


def _unjournaled_owners() -> frozenset[str]:
    """Owner call points of descriptors that legitimately produce no step row.

    Each owner here is a narrow, specific function -- on the stack only for the
    duration of its own body -- never a top-level tool entry point that would be
    on the stack for every delivery write and so exempt everything by caller
    match alone (see ``test_owner_call_point_is_never_a_top_level_tool_entry_point``
    in ``test_delivery_effect_inventory.py``, which guards this invariant at the
    registry level).
    """
    return frozenset(DELIVERY_EFFECT_REGISTRY[effect_id].owner_call_point for effect_id in unjournaled_effect_ids())


def _exemption(observation: DurableWrite) -> str | None:
    """Why this unattributed write is legitimate, or ``None`` if it is not."""
    if APPLICATION_LOG_MARKER in observation.target:
        return "S21 application log sink (unjournaled/diagnostic)"
    callers = set(observation.callers)
    owned = callers & _unjournaled_owners()
    if owned:
        return f"unjournaled descriptor owner: {min(owned)}"
    non_delivery = callers & NON_DELIVERY_CALLERS
    if non_delivery:
        return f"non-delivery seam: {min(non_delivery)}"
    return None


class _TracingConnection(sqlite3.Connection):
    """A connection that reports every ``commit`` to the active trace."""

    def __init__(self, database: str, *args: object, **kwargs: object) -> None:
        super().__init__(database, *args, **kwargs)  # type: ignore[arg-type]  # justified: sqlite3 stubs type the passthrough args loosely
        self._trw_target = str(database)
        self._trw_seen_changes = 0

    def commit(self) -> None:
        # ``total_changes`` is the connection's cumulative INSERT/UPDATE/DELETE row
        # count. Committing a read-only transaction (every recall the nudge engine
        # runs during a deliver does one) leaves it flat, and recording those would
        # bury the real mutations under noise that no boundary can own.
        trace = _ACTIVE_TRACE
        changes = self.total_changes
        if trace is not None and changes > getattr(self, "_trw_seen_changes", 0):
            trace.record("sqlite_commit", getattr(self, "_trw_target", "<unknown>"))
        self._trw_seen_changes = changes
        super().commit()


#: Set only for the duration of :func:`trace_durable_writes`; the SQLite seam is a
#: process-wide patch, so it needs a process-wide handle rather than a contextvar.
_ACTIVE_TRACE: DurableWriteTrace | None = None

_WRITER_SEAMS: tuple[str, ...] = ("write_yaml", "append_jsonl", "write_text", "ensure_dir")


@contextmanager
def trace_durable_writes() -> Iterator[DurableWriteTrace]:
    """Instrument the three durable seams for the wrapped region, then restore.

    Save-and-restore is unconditional (``finally``): a leaked patch would follow
    the writer classes into every later test in the session.
    """
    global _ACTIVE_TRACE
    from trw_mcp.state.persistence import FileEventLogger, FileStateWriter

    trace = DurableWriteTrace()
    originals: dict[str, object] = {name: getattr(FileStateWriter, name) for name in _WRITER_SEAMS}
    original_log_event = FileEventLogger.log_event
    original_connect = sqlite3.connect
    _ACTIVE_TRACE = trace
    try:
        for name in _WRITER_SEAMS:
            setattr(FileStateWriter, name, _wrap_writer(name, originals[name]))
        FileEventLogger.log_event = _wrap_log_event(original_log_event)  # type: ignore[method-assign,assignment]
        sqlite3.connect = _wrap_connect(original_connect)  # type: ignore[assignment]
        yield trace
    finally:
        _ACTIVE_TRACE = None
        for name, original in originals.items():
            setattr(FileStateWriter, name, original)
        FileEventLogger.log_event = original_log_event  # type: ignore[method-assign]
        sqlite3.connect = original_connect


def _wrap_writer(seam: str, original: object) -> object:
    def traced(self: object, path: object, *args: object, **kwargs: object) -> object:
        trace = _ACTIVE_TRACE
        if trace is not None:
            trace.record(seam, str(path))
        return original(self, path, *args, **kwargs)  # type: ignore[operator]  # justified: original is the unbound method

    return traced


def _wrap_log_event(original: object) -> object:
    def traced(self: object, events_path: object, event_type: object, data: object) -> object:
        trace = _ACTIVE_TRACE
        if trace is not None:
            trace.record("log_event", f"{events_path}#{event_type}")
        return original(self, events_path, event_type, data)  # type: ignore[operator]  # justified: original is the unbound method

    return traced


def _wrap_connect(original: object) -> object:
    def traced(database: object, *args: object, **kwargs: object) -> object:
        if "factory" in kwargs:  # respect an explicit factory rather than silently replacing it
            return original(database, *args, **kwargs)  # type: ignore[operator]  # justified: original is sqlite3.connect
        return original(database, *args, factory=_TracingConnection, **kwargs)  # type: ignore[operator]  # justified: original is sqlite3.connect

    return traced
