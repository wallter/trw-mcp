"""PRD-FIX-119 FR06 — assert the CLIENT-visible catalogue, not the server's answer.

The pre-existing surface e2e (``test_surface_authority_middleware.py``) drives
the real ``on_list_tools`` / ``on_call_tool`` hooks and asserts on the value the
server returns. A real MCP client issues *tools/list* ONCE at connect and then
relies on *notifications/tools/list_changed*; it never re-asks on its own.

This module models the client instead of the server:

* ``_FakeMcpClient`` fetches the catalogue exactly once at connect;
* the ONLY other way its catalogue can change is a ``list_changed``
  notification delivered through the real ``session.send_tool_list_changed``
  hook that ``_phase_transitions.emit_list_changed`` calls;
* every assertion reads ``client.catalogue`` — a client-side cache — never a
  fresh server list.

The load-bearing FR01 consequence is therefore stated in client terms: a client
that connects has ``trw_review`` in its very first catalogue and can dispatch it
with no reconnect and no second listing (PRD-CORE-300 S11b: ``trw_review`` is now
a plain kernel member, so this is unconditional — the surface no longer depends
on the task or run state at all).
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.middleware.surface_authority import (
    SurfaceAuthorityMiddleware,
    reset_surface_authority_state,
)
from trw_mcp.models.surface_packs import ALWAYS_ON_TOOLS
from trw_mcp.server._surface_manifest_registry import eligible_tool_names

pytestmark = pytest.mark.integration

_SESSION_ID = "sess-client-catalogue"


@dataclass
class _FakeTool:
    name: str


@dataclass
class _FakeMessage:
    name: str
    arguments: dict[str, Any] | None = None


@dataclass
class _FakeMiddlewareContext:
    message: Any = None
    fastmcp_context: Any = None


_DISPATCHED = object()


class _ClientSession:
    """The MCP session object the server notifies through.

    ``_phase_transitions.emit_list_changed`` reaches the client by calling
    ``ctx.session.send_tool_list_changed()`` — this is that real seam, not a
    stub of it.
    """

    def __init__(self, client: _FakeMcpClient) -> None:
        self._client = client

    async def send_tool_list_changed(self) -> None:
        self._client.notifications_received += 1


class _ClientContext:
    """The per-connection context the server sees for this client."""

    def __init__(self, client: _FakeMcpClient) -> None:
        self.session = _ClientSession(client)
        self._session_id = client.session_id

    @property
    def session_id(self) -> str:
        return self._session_id


@dataclass
class _FakeMcpClient:
    """A client that lists once at connect and re-lists ONLY when notified."""

    middleware: SurfaceAuthorityMiddleware
    session_id: str = _SESSION_ID
    catalogue: frozenset[str] = frozenset()
    first_catalogue: frozenset[str] = frozenset()
    list_requests: int = 0
    notifications_received: int = 0
    notification_refreshes: int = 0
    ctx: Any = field(init=False)

    def __post_init__(self) -> None:
        self.ctx = _ClientContext(self)

    async def _fetch(self) -> frozenset[str]:
        """Issue ONE *tools/list* round trip through the real middleware."""
        self.list_requests += 1

        async def call_next(_ctx: Any) -> Any:
            return [_FakeTool(name=n) for n in sorted(eligible_tool_names())]

        ctx = _FakeMiddlewareContext(fastmcp_context=self.ctx)
        tools = await self.middleware.on_list_tools(ctx, call_next)  # type: ignore[arg-type]
        return frozenset(t.name for t in tools)

    async def connect(self) -> None:
        """The MCP handshake: one *tools/list*, cached for the connection."""
        self.catalogue = await self._fetch()
        self.first_catalogue = self.catalogue

    async def drain_notifications(self) -> int:
        """Refresh the cache IFF the server pushed ``list_changed``; return the
        number of notification-driven re-lists performed.

        A client with no notification has no reason to re-list. The count of
        PENDING notifications is snapshotted before any fetch, so a re-list
        cannot manufacture its own justification.
        """
        pending = self.notifications_received - self.notification_refreshes
        refreshed = 0
        while refreshed < pending:
            refreshed += 1
            self.notification_refreshes += 1
            self.catalogue = await self._fetch()
        return refreshed

    async def dispatch(self, tool_name: str) -> Any:
        """Call a tool the client believes it has, through the real call path."""

        async def call_next(_ctx: Any) -> Any:
            return _DISPATCHED

        ctx = _FakeMiddlewareContext(
            message=_FakeMessage(name=tool_name),
            fastmcp_context=self.ctx,
        )
        return await self.middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real TRW project root with a real ``tool_resolution_mode: standard``."""
    from trw_mcp.models.config import _reset_config
    from trw_mcp.state import _pin_store as pin_store_mod
    from trw_mcp.state._paths import _pinned_runs

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    trw = tmp_path / ".trw"
    trw.mkdir(parents=True, exist_ok=True)
    (trw / "config.yaml").write_text("tool_resolution_mode: standard\n", encoding="utf-8")
    _reset_config()
    _pinned_runs.clear()
    pin_store_mod.invalidate_pin_store_cache()
    reset_surface_authority_state()
    yield tmp_path
    reset_surface_authority_state()


# ── FR06 / Acceptance 1: the connect-time catalogue ─────────────────────


@pytest.mark.asyncio
async def test_client_catalogue_contains_review_at_connect(project: Path) -> None:
    """FR01 stated in client terms: the FIRST catalogue a client ever receives
    already contains ``trw_review`` (S11b: it is a plain kernel member now, so
    this holds with no pin and no run)."""
    client = _FakeMcpClient(middleware=SurfaceAuthorityMiddleware())
    await client.connect()

    assert client.list_requests == 1, "a real client lists once at connect"
    assert "trw_review" in client.first_catalogue
    # The catalogue is genuinely BOUNDED at this point — a flag-gated pack
    # whose flag is off (dispatch_tools_exposed default False) stays masked, so
    # the line above means something.
    assert "trw_dispatch" not in client.first_catalogue
    # Derived from the live source (ALWAYS_ON_TOOLS, real config comms default
    # on) rather than a hand-picked literal — comments state why each surface
    # moved historically; a future change to either derives a new value here.
    expected = ALWAYS_ON_TOOLS | {"trw_send", "trw_inbox"}
    assert client.first_catalogue == expected & set(eligible_tool_names())
    assert {"trw_send", "trw_inbox"} <= set(client.first_catalogue)


@pytest.mark.asyncio
async def test_client_can_dispatch_review_it_can_see(project: Path) -> None:
    """FR01 / Acceptance 1: the client dispatches ``trw_review`` straight out of
    its own connect-time catalogue — no second listing, no notification, no
    reconnect."""
    client = _FakeMcpClient(middleware=SurfaceAuthorityMiddleware())
    await client.connect()

    assert "trw_review" in client.catalogue  # the client's cache, not the server's
    assert await client.dispatch("trw_review") is _DISPATCHED
    assert client.list_requests == 1
    assert client.notifications_received == 0


# ── Acceptance 9: no client-surface claim rests on an extra server list ──


def test_module_lists_only_through_the_client_refresh_path() -> None:
    """PRD-FIX-119 Acceptance 9, enforced on this module's own source.

    Every ``on_list_tools`` call site in this file must live inside
    ``_FakeMcpClient._fetch`` — the single refresh path that is either the
    connect handshake or a notification-driven re-list. A future edit that adds
    a convenience server-side re-list to "prove" a client property fails here.
    """
    source = Path(inspect.getfile(_FakeMcpClient)).read_text(encoding="utf-8")
    tree = ast.parse(source)

    fetch_ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == "_fetch":
            fetch_ranges.append((node.lineno, node.end_lineno or node.lineno))
    assert fetch_ranges, "the client's single refresh path disappeared"

    stray: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr != "on_list_tools":
            continue
        if not any(start <= node.lineno <= end for start, end in fetch_ranges):
            stray.append(node.lineno)
    assert not stray, f"on_list_tools called outside the client refresh path at line(s) {stray}"
