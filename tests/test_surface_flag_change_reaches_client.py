"""PRD-CORE-305-FR04 (B71-44): a surface-flag edit reaches a connected client.

A real MCP client lists tools once at connect and re-lists only when the server
sends ``notifications/tools/list_changed``. Before this fix the server noticed a
surface change only on the LIST path, and the process-wide config singleton
never re-read ``.trw/config.yaml``, so flipping ``dispatch_tools_exposed``
reached a client only after a reconnect.

These tests drive the real ``SurfaceAuthorityMiddleware`` against a real project
config file, edit that file between two CALLS (never a list), and read every
assertion off the client's own cached catalogue.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.middleware.surface_authority import (
    SurfaceAuthorityMiddleware,
    reset_surface_authority_state,
)
from trw_mcp.server._surface_manifest_registry import eligible_tool_names

pytestmark = pytest.mark.integration

_DISPATCHED = object()


@dataclass
class _Tool:
    name: str


@dataclass
class _Message:
    name: str
    arguments: dict[str, Any] | None = None


@dataclass
class _MiddlewareContext:
    message: Any = None
    fastmcp_context: Any = None


class _Session:
    """The session seam ``emit_list_changed`` notifies through."""

    def __init__(self, client: _Client) -> None:
        self._client = client

    async def send_tool_list_changed(self) -> None:
        self._client.notifications += 1


class _Context:
    def __init__(self, client: _Client) -> None:
        self.session = _Session(client)
        self.session_id = client.session_id


@dataclass
class _Client:
    """Lists once at connect; re-lists only when notified."""

    middleware: SurfaceAuthorityMiddleware
    session_id: str = "sess-flag-change"
    catalogue: frozenset[str] = frozenset()
    notifications: int = 0
    refreshed: int = 0
    ctx: Any = field(init=False)

    def __post_init__(self) -> None:
        self.ctx = _Context(self)

    async def _fetch(self) -> frozenset[str]:
        async def call_next(_ctx: Any) -> Any:
            return [_Tool(name=n) for n in sorted(eligible_tool_names())]

        tools = await self.middleware.on_list_tools(_MiddlewareContext(fastmcp_context=self.ctx), call_next)  # type: ignore[arg-type]
        return frozenset(t.name for t in tools)

    async def connect(self) -> None:
        self.catalogue = await self._fetch()

    async def drain(self) -> None:
        while self.refreshed < self.notifications:
            self.refreshed += 1
            self.catalogue = await self._fetch()

    async def call(self, tool_name: str) -> Any:
        async def call_next(_ctx: Any) -> Any:
            return _DISPATCHED

        ctx = _MiddlewareContext(message=_Message(name=tool_name), fastmcp_context=self.ctx)
        return await self.middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]


def _write_config(root: Path, *, dispatch: bool) -> None:
    path = root / ".trw" / "config.yaml"
    before = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text(
        f"tool_resolution_mode: standard\ndispatch_tools_exposed: {'true' if dispatch else 'false'}\n",
        encoding="utf-8",
    )
    # A coarse-mtime filesystem could leave the stamp unchanged; the size differs
    # anyway, but make the edit unambiguous without hiding the real detection.
    if path.stat().st_mtime_ns == before:
        os.utime(path, ns=(before + 1_000_000, before + 1_000_000))


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from trw_mcp.models.config import reload_config
    from trw_mcp.models.config._loader import set_config_override

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    for var in ("TRW_SESSION_ID", "TRW_DISPATCH_TOOLS_EXPOSED", "TRW_SURFACE_ROLE"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / ".trw").mkdir()
    _write_config(tmp_path, dispatch=False)
    reload_config()
    reset_surface_authority_state()
    yield tmp_path
    reset_surface_authority_state()
    set_config_override(None)


@pytest.mark.asyncio
async def test_enabling_dispatch_between_calls_notifies_and_the_relist_shows_it(project: Path) -> None:
    client = _Client(middleware=SurfaceAuthorityMiddleware())
    await client.connect()
    assert "trw_dispatch" not in client.catalogue

    assert await client.call("trw_status") is _DISPATCHED
    assert client.notifications == 0, "an unchanged surface must not notify"

    _write_config(project, dispatch=True)
    assert await client.call("trw_status") is _DISPATCHED
    assert client.notifications == 1, "the config edit must reach the client on the next CALL"

    await client.drain()
    assert "trw_dispatch" in client.catalogue
    assert await client.call("trw_dispatch") is _DISPATCHED
    assert client.notifications == 1, "one change, one notification"


@pytest.mark.asyncio
async def test_disabling_dispatch_between_calls_notifies_and_the_relist_hides_it(project: Path) -> None:
    _write_config(project, dispatch=True)
    client = _Client(middleware=SurfaceAuthorityMiddleware())
    await client.connect()
    assert "trw_dispatch" in client.catalogue

    _write_config(project, dispatch=False)
    assert await client.call("trw_status") is _DISPATCHED
    assert client.notifications == 1

    await client.drain()
    assert "trw_dispatch" not in client.catalogue
    assert await client.call("trw_dispatch") is not _DISPATCHED, "a disabled tool is denied, not executed"


@pytest.mark.asyncio
async def test_a_call_only_client_is_notified_without_ever_listing(project: Path) -> None:
    """The first call seeds silently; a later flag flip notifies on the next call."""
    client = _Client(middleware=SurfaceAuthorityMiddleware())
    assert await client.call("trw_status") is _DISPATCHED
    assert client.notifications == 0

    _write_config(project, dispatch=True)
    assert await client.call("trw_status") is _DISPATCHED
    assert client.notifications == 1


def test_injected_config_is_never_replaced_by_a_file_reload(project: Path) -> None:
    """A test- or caller-injected config stays until reload_config() clears it."""
    from trw_mcp.models.config import TRWConfig, get_config, reload_config
    from trw_mcp.models.config._loader import refresh_config_if_changed

    injected = TRWConfig(dispatch_tools_exposed=True)
    reload_config(injected)
    _write_config(project, dispatch=False)
    assert refresh_config_if_changed() is False
    assert get_config() is injected


def test_unchanged_files_keep_the_same_config_object(project: Path) -> None:
    from trw_mcp.models.config import get_config
    from trw_mcp.models.config._loader import refresh_config_if_changed

    first = get_config()
    assert refresh_config_if_changed() is False
    assert get_config() is first

    _write_config(project, dispatch=True)
    assert refresh_config_if_changed() is True
    assert get_config() is not first
    assert get_config().dispatch_tools_exposed is True


@pytest.mark.asyncio
async def test_a_project_env_edit_reaches_the_client_too(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The assess backend also reads the project ``.env``; the memo must not hide that edit."""
    from trw_mcp.models.config import reload_config

    monkeypatch.delenv("TRW_JEV_ENABLED", raising=False)
    monkeypatch.delenv("TRW_ASSESS_ENABLED", raising=False)
    (project / ".env").write_text("TRW_JEV_ENABLED=0\n", encoding="utf-8")
    reload_config()
    client = _Client(middleware=SurfaceAuthorityMiddleware())
    await client.connect()
    assert "trw_assess" not in client.catalogue

    (project / ".env").write_text("TRW_JEV_ENABLED=1\n", encoding="utf-8")
    assert await client.call("trw_status") is _DISPATCHED
    assert client.notifications == 1
    await client.drain()
    assert "trw_assess" in client.catalogue


def test_unchanged_resolution_stays_under_a_millisecond(project: Path) -> None:
    """PRD-CORE-305-NFR02: surface resolution < 1 ms per call when nothing changed."""
    import time

    middleware = SurfaceAuthorityMiddleware()
    middleware._resolve()
    runs = 200
    started = time.perf_counter()
    for _ in range(runs):
        middleware._resolve()
    per_call = (time.perf_counter() - started) / runs
    assert per_call < 0.001, f"surface resolution took {per_call * 1e6:.0f} us per call"


# ── Round-1 review fixes ────────────────────────────────────────────────


def _run_main(argv: list[str], monkeypatch: pytest.MonkeyPatch, while_serving: Any) -> None:
    """Run the real ``_cli.main()`` serve path; *while_serving* runs in place of the transport."""
    import logging
    import sys

    import structlog

    from trw_mcp.server import _cli

    def transport(**_kwargs: object) -> None:
        while_serving()

    root = logging.getLogger()
    saved_logging = (list(root.handlers), root.level)
    saved_structlog = structlog.get_config()
    with monkeypatch.context() as patch:
        patch.setattr(sys, "argv", argv)
        patch.setattr(_cli, "configure_logging", lambda **_k: None)
        patch.setattr(_cli, "_register_thread_dump_signal", lambda *_a: True)
        patch.setattr(_cli, "_check_mcp_json_portability", lambda *_a: None)
        patch.setattr(_cli, "_start_boot_sequence", lambda *_a, **_k: None)
        patch.setattr("trw_mcp.server._boot_timeline.enable_boot_timeline_emission", lambda: None)
        patch.setattr("trw_mcp.server._transport.resolve_and_run_transport", transport)
        try:
            _cli.main()
        finally:
            root.handlers[:] = saved_logging[0]
            root.setLevel(saved_logging[1])
            structlog.configure(**saved_structlog)


def test_the_real_serve_path_keeps_the_config_reloadable_and_the_cli_override(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P0: while ``trw-mcp --allow-unsigned`` serves, a file edit is picked up and
    the CLI override survives the rebuild."""
    from trw_mcp.models.config import get_config
    from trw_mcp.models.config._loader import refresh_config_if_changed

    seen: list[tuple[bool, bool]] = []

    def while_serving() -> None:
        seen.append((get_config().security.mcp.allow_unsigned, get_config().dispatch_tools_exposed))
        _write_config(project, dispatch=True)
        assert refresh_config_if_changed() is True, "the serve path disabled the file refresh"
        seen.append((get_config().security.mcp.allow_unsigned, get_config().dispatch_tools_exposed))

    _run_main(["trw-mcp", "--allow-unsigned"], monkeypatch, while_serving)
    assert seen == [(True, False), (True, True)], "the CLI override was lost, or the edit was missed"


def test_the_cli_override_does_not_outlive_main(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P2: main() owns the override's lifecycle — a later main() or caller never
    inherits an earlier ``--allow-unsigned``."""
    from trw_mcp.models.config import get_config

    seen: list[bool] = []

    def while_serving() -> None:
        seen.append(get_config().security.mcp.allow_unsigned)

    _run_main(["trw-mcp", "--allow-unsigned"], monkeypatch, while_serving)
    assert get_config().security.mcp.allow_unsigned is False, "the override outlived main()"
    _run_main(["trw-mcp"], monkeypatch, while_serving)
    assert seen == [True, False]


def test_an_override_that_calls_get_config_fails_clearly(project: Path) -> None:
    """P2: the override receives the built config; calling get_config() from it
    is refused with a clear error rather than recursing."""
    from trw_mcp.exceptions import ConfigError
    from trw_mcp.models.config import get_config
    from trw_mcp.models.config._loader import set_config_override

    def reentrant(config: Any) -> Any:
        get_config()
        return config

    set_config_override(reentrant)
    with pytest.raises(ConfigError, match="must not call get_config"):
        get_config()
    set_config_override(None)
    assert get_config().dispatch_tools_exposed is False, "a refused override must not wedge the loader"


def test_edits_across_every_retry_leave_the_config_stale_until_rebuilt(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1: when every read overlaps an edit, the published config is left
    untracked-stale so the next request rebuilds with the latest values."""
    from trw_mcp.models.config import _loader, get_config, reload_config
    from trw_mcp.models.config._loader import refresh_config_if_changed

    real_build = _loader._build_config
    state = {"reads": 0, "dispatch": False}

    def build_then_edit() -> Any:
        config = real_build()
        if state["reads"] < 10:
            state["reads"] += 1
            state["dispatch"] = not state["dispatch"]
            _write_config(project, dispatch=bool(state["dispatch"]))  # an edit after every read
        return config

    monkeypatch.setattr(_loader, "_build_config", build_then_edit)
    reload_config()
    published = get_config().dispatch_tools_exposed
    latest = bool(state["dispatch"])
    assert published is not latest, "setup: the published config should lag the file"
    state["reads"] = 10  # quiet period: no more edits
    assert refresh_config_if_changed() is True
    assert get_config().dispatch_tools_exposed is latest


def test_an_edit_during_the_build_is_not_missed(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P1: stamps are taken before the files are read, so an edit landing
    mid-build is never paired with the old values."""
    from trw_mcp.models.config import _loader, get_config, reload_config
    from trw_mcp.models.config._loader import refresh_config_if_changed

    real_build = _loader._build_config
    edited: list[bool] = []

    def build_then_edit() -> Any:
        config = real_build()  # reads dispatch: false
        if not edited:
            edited.append(True)
            _write_config(project, dispatch=True)  # lands after the read
        return config

    monkeypatch.setattr(_loader, "_build_config", build_then_edit)
    reload_config()
    get_config()
    refresh_config_if_changed()
    assert get_config().dispatch_tools_exposed is True


def test_concurrent_first_reads_build_one_config(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P1: the rebuild is serialized; racing first reads share one singleton."""
    import threading
    import time

    from trw_mcp.models.config import _loader, get_config, reload_config

    real_build = _loader._build_config
    builds: list[int] = []

    def slow_build() -> Any:
        builds.append(1)
        time.sleep(0.05)
        return real_build()

    monkeypatch.setattr(_loader, "_build_config", slow_build)
    reload_config()
    seen: list[object] = []
    threads = [threading.Thread(target=lambda: seen.append(get_config())) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(builds) == 1
    assert all(c is seen[0] for c in seen)


@pytest.mark.asyncio
async def test_a_failed_notification_is_retried_on_the_next_call(project: Path) -> None:
    """P1: a send that fails leaves the change pending instead of swallowing it."""
    client = _Client(middleware=SurfaceAuthorityMiddleware())
    await client.connect()
    failing = True

    async def flaky_send() -> None:
        if failing:
            raise RuntimeError("transport down")
        client.notifications += 1

    client.ctx.session.send_tool_list_changed = flaky_send
    _write_config(project, dispatch=True)
    await client.call("trw_status")
    assert client.notifications == 0
    failing = False
    await client.call("trw_status")
    assert client.notifications == 1, "the failed notification was never retried"


def test_a_changed_project_root_rebuilds_for_the_new_project(
    project: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1: the refresh key includes the resolved project root."""
    from trw_mcp.models.config import get_config
    from trw_mcp.models.config._loader import refresh_config_if_changed

    assert get_config().dispatch_tools_exposed is False
    other = tmp_path_factory.mktemp("other-project")
    (other / ".trw").mkdir()
    _write_config(other, dispatch=True)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(other))
    assert refresh_config_if_changed() is True
    assert get_config().dispatch_tools_exposed is True
