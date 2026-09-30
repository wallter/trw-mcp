"""The stdio server starts without fastmcp's banner and its PyPI version check (FASTMCP-BANNER-OFF).

fastmcp's ``run()`` prints a logo plus an "Update available" notice to stderr and asks PyPI for a newer fastmcp on
every start (the check lives inside the banner). A client-spawned stdio server has no terminal to read that and no
reason to make a network call, so the one place TRW starts the server asks for no banner.
"""

from __future__ import annotations

from typing import Any

import structlog


class _SpyApp:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append({"args": args, **kwargs})


def test_the_stdio_server_is_started_with_the_banner_off(monkeypatch: object) -> None:
    from _pytest.monkeypatch import MonkeyPatch

    from trw_mcp.server import _transport

    assert isinstance(monkeypatch, MonkeyPatch)
    spy = _SpyApp()
    monkeypatch.setattr(_transport, "build_served_app", lambda: spy)
    monkeypatch.setattr(_transport, "emit_boot_phase", lambda _phase: None)
    monkeypatch.setattr(_transport, "start_parent_watch", lambda: None)

    _transport.resolve_and_run_transport(debug=False, log=structlog.get_logger("test"))

    assert spy.calls == [{"args": (), "show_banner": False}]


def test_fastmcp_asks_pypi_only_from_inside_the_banner() -> None:
    """The reason banner-off is enough: no other fastmcp code path calls the version check on a server start."""
    import inspect

    from fastmcp.server.mixins import transport
    from fastmcp.utilities import cli

    source = inspect.getsource(transport)
    assert "check_for_newer_version" not in source, "fastmcp now checks for updates outside the banner; revisit"
    assert "check_for_newer_version" in inspect.getsource(cli.log_server_banner)
