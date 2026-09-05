"""PRD-FIX-119 FR06 — assert the CLIENT-visible catalogue, not the server's answer.

The pre-existing surface e2e (``test_surface_authority_middleware.py``:339)
drives its own notification by calling ``on_list_tools`` three times with
``task_type`` mutated in between, and asserts on the value the server returned
the third time. A real MCP client issues *tools/list* ONCE at connect and then
relies on *notifications/tools/list_changed*; it never re-asks on its own. That
gap is why learning L-8rUJ recorded "coding init widens to 16" as verified while
every live session sat frozen at the kernel-only surface.

This module models the client instead of the server:

* ``_FakeMcpClient`` fetches the catalogue exactly once at connect;
* the ONLY other way its catalogue can change is a ``list_changed``
  notification delivered through the real ``session.send_tool_list_changed``
  hook that ``_phase_transitions.emit_list_changed`` calls;
* every assertion reads ``client.catalogue`` — a client-side cache — never a
  fresh server list.

The load-bearing FR01 consequence is therefore stated in client terms: a client
that connects with no pinned run has ``trw_review`` in its very first catalogue
and can dispatch it with no grant, no reconnect and no second listing.

``test_client_catalogue_does_not_learn_about_a_widened_surface`` is the control
that makes the rest non-vacuous: after ``trw_init(task_type="coding")`` the
SERVER's surface genuinely contains ``trw_code_search``, and the client's
catalogue genuinely does not. A test that re-listed server-side would fail it.
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
    resolve_task_type,
)
from trw_mcp.server._surface_manifest_registry import eligible_tool_names
from trw_mcp.tools import phase_overrides

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

        A client with no notification has no reason to re-list — which is the
        whole point of FR06. This is the ONLY path that can update the cache
        after connect. The count of PENDING notifications is snapshotted before
        any fetch, so a re-list cannot manufacture its own justification: the
        LIST path itself emits ``list_changed`` when the surface moved, and
        reading ``notifications_received`` after fetching would let a stray
        re-list retroactively look notification-driven.
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
    phase_overrides.reset_overrides()
    reset_surface_authority_state()
    yield tmp_path
    phase_overrides.reset_overrides()
    reset_surface_authority_state()


def _init_coding_run(project_root: Path, session_id: str) -> Path:
    """Run the REAL ``trw_init(task_type='coding')`` and pin it to ``session_id``.

    The pin is written explicitly because this client's context is a test
    double, not a FastMCP request context, so ``trw_init`` cannot resolve the
    per-connection pin key itself (PRD-CORE-141). Every test that uses this
    helper asserts the resulting server-side ``task_type`` as a precondition, so
    the stand-in is verified rather than assumed.
    """
    from tests.conftest import extract_tool_fn, make_test_server
    from trw_mcp.state._pin_store import upsert_pin_entry

    trw_init = extract_tool_fn(make_test_server("orchestration"), "trw_init")
    result = trw_init(task_name="fix119-client-catalogue", objective="prove FR06", task_type="coding")
    run_dir = Path(str(result["run_path"]))
    assert run_dir.is_absolute() or (project_root / run_dir).exists()
    upsert_pin_entry(session_id, run_dir)
    return run_dir


# ── FR06 / Acceptance 1: the connect-time catalogue ─────────────────────


@pytest.mark.asyncio
async def test_client_catalogue_contains_review_at_connect(project: Path) -> None:
    """FR01 stated in client terms: the FIRST catalogue a client ever receives —
    no pin, no run, no grant — already contains ``trw_review``."""
    client = _FakeMcpClient(middleware=SurfaceAuthorityMiddleware())
    await client.connect()

    assert client.list_requests == 1, "a real client lists once at connect"
    assert "trw_review" in client.first_catalogue
    # The catalogue is genuinely BOUNDED at this point — this is a kernel-only
    # session, not a full-surface fail-open, so the line above means something.
    assert "trw_code_search" not in client.first_catalogue
    # 14 since PRD-CORE-246: the ``unknown`` fallback DECLARES verification
    # (FR05) and ``trw_submit_feedback`` joined the bootstrap never-hide set
    # (FR06), so a tooling-gap report is reachable from the first catalogue too.
    # The 2026-09-04 wiring-defect fix added a third bootstrap tool
    # (``trw_prd_validate``), 13 -> 14, so a coding-task session (and any
    # sub-agent it dispatches) can reach the requirement-quality validator.
    assert len(client.first_catalogue) == 14
    assert "trw_submit_feedback" in client.first_catalogue


@pytest.mark.asyncio
async def test_client_can_dispatch_review_it_can_see(project: Path) -> None:
    """FR01 / Acceptance 1: the client dispatches ``trw_review`` straight out of
    its own connect-time catalogue — no second listing, no notification, no
    ``trw_request_tool_access`` grant, no reconnect."""
    client = _FakeMcpClient(middleware=SurfaceAuthorityMiddleware())
    await client.connect()

    assert "trw_review" in client.catalogue  # the client's cache, not the server's
    assert await client.dispatch("trw_review") is _DISPATCHED
    assert client.list_requests == 1
    assert client.notifications_received == 0
    assert not phase_overrides._overrides, "no grant was needed"


@pytest.mark.asyncio
async def test_client_catalogue_survives_init_without_a_second_listing(project: Path) -> None:
    """FR06: after ``trw_init(task_type='coding')`` the client still holds
    ``trw_review`` — and it holds it because the connect-time catalogue already
    had it, not because anything re-listed."""
    client = _FakeMcpClient(middleware=SurfaceAuthorityMiddleware())
    await client.connect()

    _init_coding_run(project, client.session_id)
    # Precondition: the SERVER's view genuinely widened (task_type now resolves).
    assert resolve_task_type(session_id=client.session_id, fastmcp_context=client.ctx) == "coding"

    refreshed = await client.drain_notifications()

    assert "trw_review" in client.catalogue
    assert await client.dispatch("trw_review") is _DISPATCHED
    # The client never issued an unsolicited list: every list beyond the connect
    # one is accounted for by a notification it had already received.
    assert client.list_requests == 1 + refreshed


@pytest.mark.asyncio
async def test_client_catalogue_does_not_learn_about_a_widened_surface(project: Path) -> None:
    """The control that makes this module non-vacuous.

    After ``trw_init(task_type='coding')`` the SERVER would answer *tools/list*
    with 17 tools including ``trw_code_search``. The CLIENT's catalogue is the
    13-tool connect-time snapshot, because this helper drives ``trw_init``
    directly rather than through the middleware, so PRD-CORE-246-FR07's
    call-path push never fires for it. A test
    that asserted the server's answer under a client-shaped name would find
    ``trw_code_search`` and pass; this one must not.

    When Slice B (FR03) lands, a push arrives on the CALL path, ``drain`` does a
    notification-driven re-list, and the second branch takes over — so this
    assertion tracks the real contract in both worlds rather than pinning
    today's gap. The branch keys on the number of refreshes ``drain`` actually
    performed, never on the post-fetch notification counter, because the LIST
    path emits ``list_changed`` itself: a stray server-side re-list would
    otherwise trigger a notification and then hide behind it.
    """
    client = _FakeMcpClient(middleware=SurfaceAuthorityMiddleware())
    await client.connect()

    _init_coding_run(project, client.session_id)
    assert resolve_task_type(session_id=client.session_id, fastmcp_context=client.ctx) == "coding"

    refreshed = await client.drain_notifications()
    assert client.list_requests == 1 + refreshed, "the client issued an unsolicited tools/list"

    if refreshed == 0:
        # No push happened → the client's view is provably frozen at connect.
        assert client.catalogue == client.first_catalogue
        assert "trw_code_search" not in client.catalogue, (
            "this assertion is reading the SERVER's answer, not the client's cache"
        )
    else:
        # A push happened → the client re-listed and learned the wider surface.
        assert "trw_code_search" in client.catalogue
    # Either way, the FIX-119 invariant holds without any propagation at all.
    assert "trw_review" in client.catalogue


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
