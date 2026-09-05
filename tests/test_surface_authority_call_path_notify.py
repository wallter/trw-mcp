"""PRD-CORE-246 FR07/NFR04 — the widened surface is pushed from the CALL path.

``emit_list_changed`` was reachable from exactly one place: ``on_list_tools``.
``on_call_tool`` resolved the surface and never notified. A real MCP client
lists ONCE at connect and then relies on ``notifications/tools/list_changed``,
so a widening caused BY a tool call — ``trw_init`` creating and pinning the run
whose ``task_type`` selects the packs — was invisible to it forever. This takes
over PRD-FIX-119 FR03, which did not land.

The re-resolution must happen AFTER ``call_next``: before it the run is not yet
pinned and the surface has not yet widened, so resolving first would compare the
old surface with itself and never notify. Every test here drives the real
``SurfaceAuthorityMiddleware`` and the real ``_last_surface`` ledger.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.middleware import surface_authority as sa
from trw_mcp.middleware.surface_authority import SurfaceAuthorityMiddleware

pytestmark = pytest.mark.integration

_DISPATCHED = object()


class _Session:
    """The MCP session object ``emit_list_changed`` actually notifies through."""

    def __init__(self, recorder: _Recorder) -> None:
        self._recorder = recorder

    async def send_tool_list_changed(self) -> None:
        self._recorder.notifications += 1


class _Recorder:
    def __init__(self, session_id: str) -> None:
        self.notifications = 0
        self.session = _Session(self)
        self.session_id = session_id


class _Msg:
    def __init__(self, name: str) -> None:
        self.name = name


class _MwContext:
    def __init__(self, *, message: Any = None, fastmcp_context: Any = None) -> None:
        self.message = message
        self.fastmcp_context = fastmcp_context


async def _call_next(_ctx: object) -> object:
    return _DISPATCHED


async def _list_next(_ctx: object) -> list[Any]:
    class _T:
        def __init__(self, name: str) -> None:
            self.name = name

    from trw_mcp.server._surface_manifest_registry import eligible_tool_names

    return [_T(n) for n in sorted(eligible_tool_names())]


@pytest.fixture(autouse=True)
def _clean_ledger() -> Any:
    sa.reset_surface_authority_state()
    yield
    sa.reset_surface_authority_state()


def _pin_standard_mode(monkeypatch: pytest.MonkeyPatch, task_types: list[str | None]) -> None:
    """Make ``resolve_task_type`` return the given sequence, one per resolution.

    The middleware resolves once BEFORE ``call_next`` and once AFTER, so a
    two-element sequence models "the call changed the run's task_type".
    """
    monkeypatch.setattr(sa, "_resolve_mode", lambda: "standard")
    pending = list(task_types)

    def _resolve(**_kwargs: object) -> str | None:
        return pending.pop(0) if pending else task_types[-1]

    monkeypatch.setattr(sa, "resolve_task_type", _resolve)


# ── FR07: the widening is pushed ────────────────────────────────────────


def test_init_call_pushes_the_widened_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR07 AC1: exactly ONE notification when a call widens the surface.

    Sequence: the client lists once at connect (unknown -> ledger seeded
    silently), then calls ``trw_init``; the post-call resolution sees ``coding``
    and pushes. Before FR07 this recorded zero.
    """
    rec = _Recorder("sess-fr07-widen")
    # list: unknown. call: pre-resolve unknown, post-resolve coding.
    _pin_standard_mode(monkeypatch, [None, None, "coding"])
    mw = SurfaceAuthorityMiddleware()

    asyncio.run(mw.on_list_tools(_MwContext(fastmcp_context=rec), _list_next))  # type: ignore[arg-type]
    assert rec.notifications == 0, "the first observation seeds the ledger silently"

    result = asyncio.run(
        mw.on_call_tool(_MwContext(message=_Msg("trw_init"), fastmcp_context=rec), _call_next)  # type: ignore[arg-type]
    )

    assert result is _DISPATCHED
    assert rec.notifications == 1


def test_unchanged_surface_emits_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR07 AC2: a call that leaves the surface alone pushes nothing.

    This is the bound that keeps FR07 from becoming a refresh loop (RISK-004).
    """
    rec = _Recorder("sess-fr07-noop")
    _pin_standard_mode(monkeypatch, ["coding"])
    mw = SurfaceAuthorityMiddleware()

    asyncio.run(mw.on_list_tools(_MwContext(fastmcp_context=rec), _list_next))  # type: ignore[arg-type]
    for _ in range(5):
        asyncio.run(
            mw.on_call_tool(_MwContext(message=_Msg("trw_learn"), fastmcp_context=rec), _call_next)  # type: ignore[arg-type]
        )

    assert rec.notifications == 0


def test_first_call_without_a_prior_list_seeds_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR07 boundary: the ledger's seed-on-first-observation rule holds on the
    call path too, so a client that never listed is not spammed."""
    rec = _Recorder("sess-fr07-seed")
    _pin_standard_mode(monkeypatch, ["coding"])
    mw = SurfaceAuthorityMiddleware()

    asyncio.run(
        mw.on_call_tool(_MwContext(message=_Msg("trw_learn"), fastmcp_context=rec), _call_next)  # type: ignore[arg-type]
    )
    assert rec.notifications == 0


def test_a_raising_emitter_still_returns_the_tool_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR07 AC3 + NFR02: the push is advisory; a notification fault must not
    fail the call — and must not cause the tool to execute twice.

    The second half matters: the caller's fail-open branch re-invokes
    ``call_next``, so a push exception leaking upward would double-execute the
    tool. The push therefore carries its OWN handler.
    """
    rec = _Recorder("sess-fr07-boom")
    _pin_standard_mode(monkeypatch, [None, None, "coding"])

    async def _boom(_ctx: object) -> None:
        raise RuntimeError("notification transport exploded")

    monkeypatch.setattr(sa, "emit_list_changed", _boom)
    mw = SurfaceAuthorityMiddleware()

    calls = 0

    async def _counting_next(_ctx: object) -> object:
        nonlocal calls
        calls += 1
        return _DISPATCHED

    asyncio.run(mw.on_list_tools(_MwContext(fastmcp_context=rec), _list_next))  # type: ignore[arg-type]
    result = asyncio.run(
        mw.on_call_tool(_MwContext(message=_Msg("trw_init"), fastmcp_context=rec), _counting_next)  # type: ignore[arg-type]
    )

    assert result is _DISPATCHED
    assert calls == 1, "a push fault must not re-execute the tool"


def test_a_raising_post_call_resolver_still_returns_the_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR07 / NFR02: the same protection when the RE-RESOLUTION itself raises."""
    rec = _Recorder("sess-fr07-resolve-boom")
    monkeypatch.setattr(sa, "_resolve_mode", lambda: "standard")

    seen = 0

    def _resolve(**_kwargs: object) -> str | None:
        nonlocal seen
        seen += 1
        if seen > 1:  # the post-call resolution
            raise RuntimeError("resolver exploded after the call")
        return "coding"

    monkeypatch.setattr(sa, "resolve_task_type", _resolve)
    mw = SurfaceAuthorityMiddleware()

    calls = 0

    async def _counting_next(_ctx: object) -> object:
        nonlocal calls
        calls += 1
        return _DISPATCHED

    result = asyncio.run(
        mw.on_call_tool(_MwContext(message=_Msg("trw_learn"), fastmcp_context=rec), _counting_next)  # type: ignore[arg-type]
    )

    assert result is _DISPATCHED
    assert calls == 1


def test_mode_all_is_still_a_strict_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    """The documented operator escape is unchanged: ``tool_resolution_mode=all``
    neither masks nor notifies."""
    rec = _Recorder("sess-fr07-all")
    monkeypatch.setattr(sa, "_resolve_mode", lambda: "all")
    mw = SurfaceAuthorityMiddleware()

    result = asyncio.run(
        mw.on_call_tool(_MwContext(message=_Msg("trw_probe"), fastmcp_context=rec), _call_next)  # type: ignore[arg-type]
    )

    assert result is _DISPATCHED
    assert rec.notifications == 0


def test_notify_fires_on_a_real_trw_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR07 end-to-end: the REAL ``trw_init`` runs inside ``call_next`` and the
    resulting task_type shift is what triggers the push.

    ``resolve_task_type`` is NOT patched here — it reads the pin the tool wrote.
    """
    from tests.conftest import extract_tool_fn, make_test_server
    from trw_mcp.models.config import _reset_config
    from trw_mcp.state import _pin_store as pin_store_mod
    from trw_mcp.state._paths import _pinned_runs
    from trw_mcp.state._pin_store import upsert_pin_entry

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    (tmp_path / ".trw").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".trw" / "config.yaml").write_text("tool_resolution_mode: standard\n", encoding="utf-8")
    _reset_config()
    _pinned_runs.clear()
    pin_store_mod.invalidate_pin_store_cache()

    rec = _Recorder("sess-fr07-real")
    mw = SurfaceAuthorityMiddleware()

    # Connect-time listing on the unresolvable surface seeds the ledger.
    asyncio.run(mw.on_list_tools(_MwContext(fastmcp_context=rec), _list_next))  # type: ignore[arg-type]
    assert rec.notifications == 0

    trw_init = extract_tool_fn(make_test_server("orchestration"), "trw_init")

    async def _real_init(_ctx: object) -> object:
        created = trw_init(task_name="fr07-real", objective="implement the parser", task_type="coding")
        upsert_pin_entry(rec.session_id, Path(str(created["run_path"])))
        return _DISPATCHED

    result = asyncio.run(
        mw.on_call_tool(_MwContext(message=_Msg("trw_init"), fastmcp_context=rec), _real_init)  # type: ignore[arg-type]
    )

    assert result is _DISPATCHED
    assert sa.resolve_task_type(session_id=rec.session_id, fastmcp_context=rec) == "coding"
    assert rec.notifications == 1, "the real task_type shift did not push the widened surface"


# ── NFR04: the ledger stays process-local, and nothing new is persisted ─


def test_per_process_ledger_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR04 AC1: two sessions resolve independently.

    ``_last_surface`` is keyed by session id and is process-local by design.
    FR07 must not promote it to shared state: session B's notification must come
    from B's own call path, never from A's ledger entry.
    """
    a, b = _Recorder("sess-A"), _Recorder("sess-B")
    monkeypatch.setattr(sa, "_resolve_mode", lambda: "standard")

    per_session: dict[str, list[str | None]] = {"sess-A": [None, None, "coding"], "sess-B": ["docs"]}

    def _resolve(*, session_id: str = "", fastmcp_context: object | None = None) -> str | None:
        queue = per_session[session_id]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(sa, "resolve_task_type", _resolve)
    mw = SurfaceAuthorityMiddleware()

    asyncio.run(mw.on_list_tools(_MwContext(fastmcp_context=a), _list_next))  # type: ignore[arg-type]
    asyncio.run(mw.on_list_tools(_MwContext(fastmcp_context=b), _list_next))  # type: ignore[arg-type]
    asyncio.run(
        mw.on_call_tool(_MwContext(message=_Msg("trw_init"), fastmcp_context=a), _call_next)  # type: ignore[arg-type]
    )

    assert a.notifications == 1
    assert b.notifications == 0, "session B observed session A's ledger entry"
    assert set(sa._last_surface) == {"sess-A", "sess-B"}
    assert sa._last_surface["sess-A"] != sa._last_surface["sess-B"]


def test_fr07_adds_no_persistent_writer() -> None:
    """NFR04 AC2: a grep-absent assertion over the middleware source.

    The change adds no write to ``.trw/runtime/pins.json``, to any ``run.yaml``,
    or to the memory database — the notification is derived state only.
    """
    import inspect as _inspect

    source = _inspect.getsource(sa)
    for forbidden in ("upsert_pin_entry", "write_yaml", "write_text", "FileStateWriter", "pins.json"):
        assert forbidden not in source, f"surface_authority must not persist state; found {forbidden!r}"
    # ``run.yaml`` appears, but only through the READER (resolve_task_type).
    assert "FileStateReader().read_yaml" in source
    assert "FileStateWriter" not in source
