"""The slice-e memory fixtures do what their contract says (tests/_memory_fixtures.py)."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from tests._memory_fixtures import FAKE_NAMESPACE, DaemonCheckout
from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.state._tier_routing import USER_NAMESPACE
from trw_mcp.state.memory_adapter import store_learning

pytestmark = pytest.mark.unit


def test_fake_memory_store_receives_every_write(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()

    assert store_learning(trw_dir, "L-fx1", "Fake summary", "detail")["status"] == "recorded"

    assert fake_memory_store.get("L-fx1") is not None
    assert (FAKE_NAMESPACE, "L-fx1") in fake_memory_store.rows


def test_daemon_checkout_writes_to_its_own_namespace_and_never_a_file(
    daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _refuse(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        raise AssertionError("the checkout opened a SQLite connection")

    monkeypatch.setattr("trw_memory.storage._connection.connect", _refuse)

    assert store_learning(daemon_checkout.trw_dir, "L-fx2", "Daemon summary", "detail")["status"] == "recorded"

    monkeypatch.undo()
    row = asyncio.run(daemon_checkout.client.get("L-fx2", daemon_checkout.namespace))
    assert row["entry"]["content"] == "Daemon summary"
    assert not list(daemon_checkout.trw_dir.rglob("memory.db"))


@pytest.mark.parametrize("run", ["first", "second"])
def test_daemon_checkout_starts_with_an_empty_user_namespace(daemon_checkout: DaemonCheckout, run: str) -> None:
    client = daemon_checkout.client

    async def _user_rows() -> list[object]:
        return list((await client.list_page(USER_NAMESPACE, 10, None))["entries"])

    assert asyncio.run(_user_rows()) == []
    asyncio.run(client.store(f"leftover {run}", namespace=USER_NAMESPACE))
    assert len(asyncio.run(_user_rows())) == 1
