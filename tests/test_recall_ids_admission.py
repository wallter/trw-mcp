"""PRD-CORE-294 FR01: ``trw_recall(ids=...)`` admits exactly what search admits.

Each filter search applies (namespace, status, temporal validity, expiry, system
canaries, the recall filter, store tamper on either tier) is exercised through
the REAL registered tool: a row search would never return must come back under
``missing_ids``, indistinguishable from an id no store holds.

PRD-CORE-280: every test runs through ``daemon_checkout`` (the ``checkout``
fixture below), the real store. The representative-row, twin and cap rules are
the store contract's (``test_store_contract``, fake and daemon alike); rows
twinned across two ``user:<name>`` namespaces of one store cannot exist on the
daemon, whose grant holds one project namespace and ``user:local``. The recall
filter a row passes through is trw-memory's (``test_injection_scan_surface``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from tests._memory_fixtures import DaemonCheckout, MemoryDaemon, attach_checkout
from trw_mcp.models.config import _reset_config
from trw_mcp.state import memory_adapter


@pytest.fixture()
def checkout(daemon_checkout: DaemonCheckout) -> DaemonCheckout:
    """The daemon-route equivalent of ``trw_dir``: a real checkout with L-ok seeded.

    ``_by_ids`` below drives the registered ``trw_recall`` tool, which resolves
    its own trw_dir via the zero-arg ``resolve_trw_dir()`` rather than taking one
    as an argument. The suite's own ``_isolate_trw_dir`` autouse fixture (conftest
    + ``tests/_path_isolation``) points that resolver's stand-in at bare
    ``tmp_path``, not ``tmp_path/repo/.trw`` -- ``daemon_checkout``'s own
    directory -- so it must be re-pointed here at the checkout's actual root,
    using that module's own public API (no edit to the shared isolation file).
    """
    from tests import _path_isolation

    _path_isolation.set_current_root(daemon_checkout.trw_dir.parent)
    asyncio.run(daemon_checkout.client.store("admitted row", daemon_checkout.namespace, entry_id="L-ok"))
    return daemon_checkout


def _store(checkout: DaemonCheckout, entry_id: str, content: str, **kwargs: Any) -> None:
    asyncio.run(checkout.client.store(content, checkout.namespace, entry_id=entry_id, **kwargs))


def _store_user(checkout: DaemonCheckout, entry_id: str, content: str, **kwargs: Any) -> None:
    from trw_mcp.state._tier_routing import USER_NAMESPACE

    asyncio.run(checkout.client.store(content, USER_NAMESPACE, entry_id=entry_id, **kwargs))


def _correct(checkout: DaemonCheckout, entry_id: str, namespace: str, patch: dict[str, Any]) -> None:
    asyncio.run(checkout.client.update(entry_id, namespace, patch))


def _by_ids(ids: list[str]) -> dict[str, Any]:
    from tests.conftest import extract_tool_fn, make_test_server

    return extract_tool_fn(make_test_server("learning"), "trw_recall")(query="", ids=ids)


def _searchable_ids(trw_dir: Path) -> set[str]:
    return {str(row["id"]) for row in memory_adapter.recall_learnings(trw_dir, "*", status="active", max_results=50)}


def _seed_other_namespace(checkout: DaemonCheckout, daemon: MemoryDaemon, tmp_path: Path) -> None:
    namespace, client = attach_checkout(tmp_path / "other" / ".trw", daemon)
    asyncio.run(client.store("another namespace", namespace, entry_id="L-x"))
    assert asyncio.run(client.get("L-x", namespace)).get("status") == "ok", "precondition: the row exists"


def _seed_resolved(checkout: DaemonCheckout, _daemon: MemoryDaemon, _tmp_path: Path) -> None:
    _store(checkout, "L-x", "resolved row")
    _correct(checkout, "L-x", checkout.namespace, {"status": "resolved"})


def _seed_superseded(checkout: DaemonCheckout, _daemon: MemoryDaemon, _tmp_path: Path) -> None:
    _store(checkout, "L-x", "superseded row")
    _correct(checkout, "L-ok", checkout.namespace, {"supersedes": "L-x"})


def _seed_expired(checkout: DaemonCheckout, _daemon: MemoryDaemon, _tmp_path: Path) -> None:
    _store(checkout, "L-x", "expired row", expires="2022-01-01")


def _seed_canary(checkout: DaemonCheckout, _daemon: MemoryDaemon, _tmp_path: Path) -> None:
    _store(checkout, "L-x", "canary row", metadata={"system_canary": "true"})


@pytest.mark.parametrize(
    "seed",
    [_seed_other_namespace, _seed_resolved, _seed_superseded, _seed_expired, _seed_canary],
    ids=["namespace", "status", "temporal", "expiry", "system-canary"],
)
def test_ids_refuse_every_row_search_filters_out(
    checkout: DaemonCheckout, memory_daemon: MemoryDaemon, tmp_path: Path, seed: Any
) -> None:
    seed(checkout, memory_daemon, tmp_path)
    if seed is not _seed_other_namespace:
        held = asyncio.run(checkout.client.get("L-x", checkout.namespace))
        assert held.get("status") == "ok", f"precondition: the daemon stored the row ({held})"

    result = _by_ids(["L-ok", "L-x"])

    assert [row["id"] for row in result["learnings"]] == ["L-ok"]
    assert result["missing_ids"] == ["L-x"]
    assert "L-x" not in _searchable_ids(checkout.trw_dir)


def _by_ids_with(ids: list[str], **kwargs: Any) -> dict[str, Any]:
    from tests.conftest import extract_tool_fn, make_test_server

    return extract_tool_fn(make_test_server("learning"), "trw_recall")(query="", ids=ids, **kwargs)


def test_ids_skip_the_user_store_when_its_recall_cap_is_zero(
    checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store_user(checkout, "L-user", "portable directive")
    assert [row["id"] for row in _by_ids(["L-user"])["learnings"]] == ["L-user"]

    monkeypatch.setenv("TRW_RECALL_USER_TIER_CAP", "0")
    _reset_config()

    assert _by_ids(["L-user"])["missing_ids"] == ["L-user"]


def test_ids_honor_the_status_argument_like_search(checkout: DaemonCheckout) -> None:
    _store(checkout, "L-old", "obsolete row")
    _correct(checkout, "L-old", checkout.namespace, {"status": "obsolete"})

    assert _by_ids(["L-old"])["missing_ids"] == ["L-old"]
    assert [row["id"] for row in _by_ids_with(["L-old"], status="obsolete")["learnings"]] == ["L-old"]
