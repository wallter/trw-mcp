"""The boot timeline: five named phase events on one monotonic origin (PRD-CORE-248 FR02).

Before this, the only boot event was ``trw_server_initialized``, and it carried
no timing at all. Measured cold start: 1.106 s of module import against a
1116.3 ms ``initialize`` reply — i.e. **99 % of the pre-``initialize`` window is
import**, and nothing in the log stream said so. An operator who saw a 16.5 s
handshake could not tell whether import, config load, the lifespan, or lock
contention consumed it, which is why that report was unreproducible rather than
fixed. The timeline exists so the next occurrence is attributable.

Five phases, in the order they occur:

``import_complete``
    ``trw_mcp.server._app``'s dependency imports are done (fastmcp, trw_memory,
    config, middleware) — emitted immediately before ``create_app()`` runs.
``app_constructed``
    The FastMCP instance exists and its tool surface is registered.
``transport_ready``
    ``resolve_and_run_transport`` is about to hand control to ``mcp.run()``.
``initialize_answered``
    The client's ``notifications/initialized`` arrived, which by protocol can
    only happen after it received the ``initialize`` result.
``deferred_work_complete``
    The FR01 post-``initialize`` step finished.

Buffering, and why it is not optional
-------------------------------------
The first two phases are emitted at MODULE IMPORT time, which on the serve path
happens after ``configure_logging`` — but on any other import path (a test, a
subcommand, ``fastmcp run``) it happens BEFORE. structlog's unconfigured default
is a ``PrintLogger`` writing to **stdout**, and stdout on this process is the
JSON-RPC channel. Emitting an unbuffered event there would corrupt the MCP
stream. Events are therefore recorded into a bounded buffer and only rendered
once :func:`enable_boot_timeline_emission` is called from the CLI, after logging
is configured.

The origin is captured when THIS module is imported, and every event names it in
an ``origin`` field rather than implying it covers process spawn — the true
spawn instant cannot be self-reported by a process that is still importing, and
must come from the client or the probe harness.
"""

from __future__ import annotations

import threading
import time

import structlog

__all__ = [
    "BOOT_ORIGIN_LABEL",
    "BOOT_PHASES",
    "elapsed_ms",
    "emit_boot_phase",
    "enable_boot_timeline_emission",
    "recorded_boot_phases",
]

#: Names the point ``elapsed_ms`` is measured from. Reported on every event so a
#: reader never has to assume it means "since process spawn" — it does not.
BOOT_ORIGIN_LABEL = "trw_mcp.server._boot_timeline import"

#: The five phases, in chronological order. Consumers (the FR02 contract test,
#: the operator runbook) read this rather than re-listing the names.
BOOT_PHASES: tuple[str, ...] = (
    "import_complete",
    "app_constructed",
    "transport_ready",
    "initialize_answered",
    "deferred_work_complete",
)

#: Single monotonic origin for the whole timeline, captured at import.
_ORIGIN = time.monotonic()

#: Cap on buffered pre-logging events. The timeline is five events; anything
#: beyond that is a caller bug, and an unbounded buffer on a boot path is not
#: worth the risk of one.
_BUFFER_LIMIT = 32

_lock = threading.Lock()
_emission_enabled = False
_buffered: list[tuple[str, int]] = []
_recorded: list[tuple[str, int]] = []


def elapsed_ms() -> int:
    """Milliseconds since the boot-timeline origin."""
    return int((time.monotonic() - _ORIGIN) * 1000)


def emit_boot_phase(phase: str) -> int:
    """Record (and, once logging is configured, log) one ``boot_phase`` event.

    Returns the recorded ``elapsed_ms`` so a caller can use the same number it
    logged. Never raises: a boot-observability failure must not affect boot.
    """
    ms = elapsed_ms()
    with _lock:
        _recorded.append((phase, ms))
        if not _emission_enabled:
            if len(_buffered) < _BUFFER_LIMIT:
                _buffered.append((phase, ms))
            return ms
    _log_phase(phase, ms)
    return ms


def enable_boot_timeline_emission() -> None:
    """Flush buffered phases and render every later one immediately.

    Called from the CLI right after ``configure_logging``. Idempotent.
    """
    global _emission_enabled
    with _lock:
        if _emission_enabled:
            return
        _emission_enabled = True
        pending = list(_buffered)
        _buffered.clear()
    for phase, ms in pending:
        _log_phase(phase, ms)


def recorded_boot_phases() -> tuple[tuple[str, int], ...]:
    """Every phase recorded so far as ``(phase, elapsed_ms)``, in emission order.

    Exposed for the FR02 contract test and for in-process diagnostics; the log
    stream remains the operator-facing surface.
    """
    with _lock:
        return tuple(_recorded)


def _log_phase(phase: str, ms: int) -> None:
    # Resolved fresh on every call, never through the module-level proxy: the
    # first two phases are emitted at IMPORT time, which binds a cached logger
    # carrying whatever processor chain existed then. A later
    # ``configure_logging`` (production) or ``capture_logs`` (tests) would not
    # reach it, so the timeline would be silently invisible in exactly the two
    # places it has to be visible.
    structlog.get_logger(__name__).info("boot_phase", phase=phase, elapsed_ms=ms, origin=BOOT_ORIGIN_LABEL)
