"""A daemon test leaves no session socket open after its teardown.

``daemon_store_for`` caches one client per grant, each holding one open MCP session to
the daemon. Every ``daemon_checkout`` test mints a fresh grant, so each test that reached
the store left one socket open until the pytest process ended: 50 after 50 tests, a slow
descriptor exhaustion in one long process.

The check runs when this module is torn down, which is after the teardown of every test
in it, autouse fixtures included. It looks only at what the store calls of this module
opened: the clients they cached must be retired, and the sockets that appeared during
those calls must be closed. A socket some other thread opened meanwhile is not counted.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from tests._memory_fixtures import DaemonCheckout
from trw_mcp.state import _daemon_store
from trw_mcp.state._store_selection import selected_store


def _open_sockets() -> set[int]:
    """The descriptors of this process that are sockets. Only ever looked at, never closed."""
    found: set[int] = set()
    for name in os.listdir("/dev/fd"):
        try:
            if stat.S_ISSOCK(os.fstat(int(name)).st_mode):
                found.add(int(name))
        except (ValueError, OSError):  # trw-fail-silent-allow: the listing's own descriptor is gone by now
            continue
    return found


@dataclass
class _Seen:
    """What this module's store calls opened: the sockets that appeared and the clients that were cached."""

    sockets: set[int] = field(default_factory=set)
    clients: list[Any] = field(default_factory=list)


@pytest.fixture(scope="module")
def sockets_opened_by_this_module() -> Iterator[_Seen]:
    # The daemon-call loop is one per process and keeps its own wake-up socket pair:
    # started here so no store call below is the one that opens it.
    _daemon_store._daemon_loop()
    seen = _Seen()
    yield seen
    assert seen.sockets and seen.clients, "no test of this module held a daemon session; the check measured nothing"
    unretired = [client for client in seen.clients if not client._sessions.retired]
    assert not unretired, f"{len(unretired)} client(s) this module's tests cached were never retired"
    left = seen.sockets & _open_sockets()
    assert not left, f"{len(left)} socket(s) opened by this module's daemon tests are still open after their teardown"


def _use_the_store(checkout: DaemonCheckout, seen: _Seen) -> int:
    """One store read over the client's held session; how many sockets it opened."""
    before = _open_sockets()
    store, namespace = selected_store(checkout.trw_dir)
    # A read the daemon serves over the client's held session (a status or a page is a plain POST).
    assert namespace == checkout.namespace
    assert store.get("L-absent") is None
    opened = _open_sockets() - before
    seen.sockets |= opened
    seen.clients += [client for client, _ in _daemon_store._clients.values() if client not in seen.clients]
    return len(opened)


def test_a_daemon_checkout_test_holds_a_session_while_it_runs(
    sockets_opened_by_this_module: _Seen, daemon_checkout: DaemonCheckout
) -> None:
    assert _use_the_store(daemon_checkout, sockets_opened_by_this_module) > 0


def test_a_second_daemon_checkout_test_gets_a_session_of_its_own(
    sockets_opened_by_this_module: _Seen, daemon_checkout: DaemonCheckout
) -> None:
    assert _use_the_store(daemon_checkout, sockets_opened_by_this_module) > 0


@pytest.mark.parametrize("configured_checkout", [{}], indirect=True)
def test_a_checkout_on_a_daemon_of_its_own_holds_a_session_too(
    sockets_opened_by_this_module: _Seen, configured_checkout: DaemonCheckout
) -> None:
    assert _use_the_store(configured_checkout, sockets_opened_by_this_module) > 0
