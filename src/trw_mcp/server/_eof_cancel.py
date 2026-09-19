"""stdio-EOF cancellation adapter for FastMCP 3.x (PRD-CORE-274 Amendment 01, FR11).

FastMCP 3.x ``LowLevelServer.run`` overrides the MCP SDK's ``Server.run`` and
drops the SDK's ``finally: tg.cancel_scope.cancel()``: when the client closes
stdin, the receive loop ends but the handler task group JOINS the in-flight
handlers, so a tool still sleeping (an FR11 bounded wait) runs to its deadline.
This module restores exactly the SDK's behaviour, per FastMCP instance, without
copying ``run``:

* a receive-stream proxy that delegates ``receive``/``aclose`` and, on the real
  ``EndOfStream``, cancels FastMCP's own handler task group — the object FastMCP
  stores as ``session._subscription_task_group`` before its message loop — then
  re-raises, so the SDK session tears down exactly as today;
* a stateless capture middleware at the head of the chain that records the
  session (any message, ``initialize`` included) and validates the cancel target
  BEFORE the first post-initialisation handler-group request does downstream work.

Contract: in-flight handler-group work of an initialised session is cancelled at
EOF. Non-goal: a middleware that blocks ``initialize`` blocks the SDK receive
loop itself; the proxy cannot observe EOF there and this adapter claims nothing
for it. Only the handler task group is cancelled, never an outer scope, so the
awaited session/lifespan teardown inside ``run`` survives.

Supported contract: the declared range ``fastmcp>=3.2.0,<4.0.0`` (both ends
read: they override ``run`` and set ``_subscription_task_group``). When FastMCP
does not override ``run`` (4.x delegates to the SDK, which cancels on EOF) the
install is a no-op. Removal condition: the separately backlogged FastMCP 4
migration; the no-op branch makes the adapter inert there rather than wrong.
"""

from __future__ import annotations

import contextvars
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import anyio
import structlog
from anyio.abc import ObjectReceiveStream, TaskGroup
from fastmcp import FastMCP
from fastmcp.server.low_level import LowLevelServer
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from mcp.server.lowlevel.server import Server as _SdkServer
from packaging.version import Version

T = TypeVar("T")
_logger = structlog.get_logger(__name__)

#: Declared, verified contract. Outside it an overriding ``run`` is refused at boot.
SUPPORTED_MAJOR = 3
_INSTALLED_MARK = "_trw_eof_cancel_installed"


class EOFCancelUnsupported(RuntimeError):
    """The captured session offers no cancellable handler task group.

    Raised before downstream work on the first post-initialisation request, so
    an unsupported FastMCP interface fails loudly instead of leaving a wait
    running to its deadline after a disconnect.
    """


@dataclass
class _RunHolder:
    """Per-``run()`` state; one instance per transport lifetime, never shared."""

    session: Any | None = None
    validated: bool = False


class _EOFSignallingReceiveStream(ObjectReceiveStream[T]):
    """Delegate everything; on the genuine end of stream, fire the callback, then re-raise.

    The SDK session consumes its read stream through ``async with`` and
    ``async for``; both come from the ``ObjectReceiveStream`` ABC on top of the
    two abstract methods below, so no other behaviour is intercepted.
    """

    def __init__(self, inner: ObjectReceiveStream[T], on_eof: Callable[[], None]) -> None:
        self._inner = inner
        self._on_eof = on_eof
        self._fired = False

    async def receive(self) -> T:
        try:
            return await self._inner.receive()
        except anyio.EndOfStream:
            if not self._fired:  # exactly once, however often the closed stream is polled
                self._fired = True
                self._on_eof()
            raise

    async def aclose(self) -> None:
        await self._inner.aclose()


class _SessionCapture(Middleware):
    """Record the session early; validate the cancel target late (before handler work)."""

    def __init__(self, var: contextvars.ContextVar[_RunHolder | None]) -> None:
        self._var = var

    async def on_message(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        holder = self._var.get()
        if holder is not None and context.fastmcp_context is not None:
            try:
                session = context.fastmcp_context.session
            except RuntimeError:
                session = None  # no request context on this message: nothing to record yet
            if session is not None and holder.session is None:
                holder.session = session
            # trw:intentional `initialize` runs inside the SDK receive loop, BEFORE
            # FastMCP assigns the handler task group; validating there could refuse a
            # legitimate server on a scheduling race. Handler-group requests are spawned
            # by the loop that starts only after the assignment, so this is safe.
            if session is not None and not holder.validated and context.method != "initialize":
                if not isinstance(getattr(session, "_subscription_task_group", None), TaskGroup):
                    raise EOFCancelUnsupported(
                        "FastMCP session exposes no handler task group; disconnect cancellation is unavailable "
                        f"on this FastMCP build (supported: fastmcp {SUPPORTED_MAJOR}.x with LowLevelServer.run override)"
                    )
                holder.validated = True
        return await call_next(context)


def _resolve_eof(holder: _RunHolder) -> None:
    session = holder.session
    if session is None:
        _logger.debug("eof_cancel_no_session")  # EOF before any message: nothing in flight
        return
    task_group = getattr(session, "_subscription_task_group", None)
    if isinstance(task_group, TaskGroup):
        task_group.cancel_scope.cancel()
        _logger.info("eof_cancel_handler_group_cancelled")
        return
    # Only `initialize` can have run before the task group exists: no handler work to cancel.
    _logger.debug("eof_cancel_pre_handler")


def _overrides_run(low_level: LowLevelServer[Any, Any]) -> bool:
    """Structural check by attribute identity, not source text.

    When FastMCP inherits the SDK's ``run`` (4.x), the SDK's own ``finally``
    cancels in-flight handlers on EOF and this adapter must stay out.
    """
    return type(low_level).run is not _SdkServer.run


def install_eof_cancel(app: FastMCP, *, fastmcp_version: str | None = None) -> bool:
    """Install the adapter on *app*'s low-level server; return whether it was installed.

    Idempotent: a second call on the same app changes nothing. ``fastmcp_version``
    exists for tests; production reads the installed distribution.
    """
    from fastmcp import __version__ as _installed

    low_level = app._mcp_server  # the seam FastMCP exposes; no public accessor exists
    if getattr(low_level, _INSTALLED_MARK, False):
        return True
    if not _overrides_run(low_level):
        _logger.info("eof_cancel_not_needed", reason="LowLevelServer.run is the SDK run, which cancels on EOF")
        return False
    major = Version(fastmcp_version or _installed).major
    if major != SUPPORTED_MAJOR:
        raise RuntimeError(
            f"trw-mcp's EOF cancellation adapter was verified only for fastmcp {SUPPORTED_MAJOR}.x "
            f"(declared range >=3.2.0,<4.0.0: the floor is enforced by packaging metadata, the major by this "
            f"guard); installed {fastmcp_version or _installed} overrides run() "
            "but is outside that range — re-verify the adapter before widening the dependency bound"
        )

    var: contextvars.ContextVar[_RunHolder | None] = contextvars.ContextVar(f"trw_eof_cancel_{id(app)}", default=None)
    # Head of the chain: a later middleware that blocks the first tools/call must not
    # delay the capture past EOF (fastmcp/server/server.py _apply_middleware wraps in
    # reversed order, so index 0 is the outermost layer).
    app.middleware.insert(0, _SessionCapture(var))
    original_run: Callable[..., Awaitable[Any]] = low_level.run

    async def run(
        read_stream: ObjectReceiveStream[Any],
        write_stream: Any,
        initialization_options: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        holder = _RunHolder()
        token = var.set(holder)
        try:
            proxy: _EOFSignallingReceiveStream[Any] = _EOFSignallingReceiveStream(
                read_stream, lambda: _resolve_eof(holder)
            )
            return await original_run(proxy, write_stream, initialization_options, *args, **kwargs)
        finally:
            var.reset(token)

    # Instance-level shadow of the bound method: per app, no class or global patch.
    low_level.run = run  # type: ignore[method-assign]  # deliberate instance shadowing of a method
    setattr(low_level, _INSTALLED_MARK, True)
    _logger.info("eof_cancel_installed", fastmcp=fastmcp_version or _installed)
    return True


__all__ = ["SUPPORTED_MAJOR", "EOFCancelUnsupported", "install_eof_cancel"]
