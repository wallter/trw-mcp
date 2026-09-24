"""The two ways a trw-mcp test reaches memory once the in-process SQLite store is gone (PRD-CORE-280 slice e).

``fake_memory_store``
    For a test that does not care how memory behaves. ``selected_store`` returns a
    fresh in-memory ``FakeMemoryStore`` pinned to ``FAKE_NAMESPACE`` for every
    checkout; assert on ``store.rows`` / ``store.calls``. No daemon, no file.

``daemon_checkout``
    For a test of real store behaviour (the write gate, recall ranking, dedup,
    sync). ``tmp_path/repo/.trw`` is a migrated checkout pinned to a namespace
    unique to the test and granted it plus ``user:local``, served by ONE real
    daemon per test session (per xdist worker). ``user:local`` is emptied before
    and after the test. The daemon is never autostarted from a test.

``configured_checkout`` (indirect-parametrized with MEMORY_* settings)
    ``daemon_checkout`` on a daemon of its own, started under those settings. The
    client matches them, so the attach opens and the test sees the daemon enforce
    a daemon-wide security setting (PRD-CORE-298 FR07).

All set ``TRW_PROJECT_ROOT`` to the checkout. A test that builds its own
``.trw`` elsewhere uses ``attach_checkout(trw_dir, paths)`` on the
``memory_daemon`` paths instead.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from trw_memory.daemon import DaemonPaths, mint_grant, write_checkout_grant
from trw_memory.daemon.client import DaemonClient

from tests._memory_daemon import running_daemon
from tests._memory_store_fake import FakeMemoryStore
from tests._path_isolation import set_current_root
from trw_mcp.models.config import reload_config
from trw_mcp.state import _daemon_store, _store_selection
from trw_mcp.state._tier_routing import USER_NAMESPACE

FAKE_NAMESPACE = "project:test"


@dataclass(frozen=True)
class MemoryDaemon:
    """The session daemon: its paths and the ``TRW_USER_DIR`` it runs under."""

    paths: DaemonPaths
    user_dir: Path


@pytest.fixture(scope="session")
def memory_daemon(tmp_path_factory: pytest.TempPathFactory) -> Iterator[MemoryDaemon]:
    user_dir = tmp_path_factory.mktemp("memory-daemon")
    with running_daemon(user_dir) as paths:
        yield MemoryDaemon(paths, user_dir)


def attach_checkout(trw_dir: Path, daemon: MemoryDaemon) -> tuple[str, DaemonClient]:
    """Pin *trw_dir* to a fresh namespace and grant it plus ``user:local``; returns (namespace, client)."""
    namespace = f"project:t{uuid.uuid4().hex[:12]}"
    trw_dir.mkdir(parents=True, exist_ok=True)
    config = trw_dir / "config.yaml"
    existing = config.read_text(encoding="utf-8") if config.exists() else ""
    config.write_text(f"{existing}project_namespace: {namespace}\n", encoding="utf-8")
    token = mint_grant(daemon.paths, [namespace, USER_NAMESPACE], root=trw_dir.parent)
    write_checkout_grant(trw_dir, token)
    return namespace, DaemonClient(token, paths=daemon.paths)


def _empty_user_namespace(client: DaemonClient) -> None:
    async def _wipe() -> None:
        while True:
            page = await client.list_page(USER_NAMESPACE, 1000, None)
            if not page["entries"]:
                return
            for row in page["entries"]:
                await client.forget(str(row["id"]), USER_NAMESPACE)

    asyncio.run(_wipe())


@dataclass(frozen=True)
class DaemonCheckout:
    trw_dir: Path
    namespace: str
    client: DaemonClient


@pytest.fixture
def daemon_checkout(
    memory_daemon: MemoryDaemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[DaemonCheckout]:
    # The session daemon's user dir, not the per-test one, so the client finds it.
    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path / "repo"))
    monkeypatch.delenv("TRW_PROJECT_NAMESPACE", raising=False)

    def _no_autostart(_paths: DaemonPaths) -> None:
        raise AssertionError("a test tried to start a second memory daemon")

    monkeypatch.setattr("trw_memory.daemon.client.start_daemon_detached", _no_autostart)
    trw_dir = tmp_path / "repo" / ".trw"
    namespace, client = attach_checkout(trw_dir, memory_daemon)
    _empty_user_namespace(client)
    reload_config()
    try:
        yield DaemonCheckout(trw_dir, namespace, client)
    finally:
        _empty_user_namespace(client)
        reload_config()


@pytest.fixture
def configured_checkout(
    tmp_path_factory: pytest.TempPathFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Iterator[DaemonCheckout]:
    """A checkout on its own daemon, started under the MEMORY_* settings the test names.

    The client runs under the same environment, so the settings match and the attach
    opens; the test then observes the daemon enforcing them.
    """
    for name, value in request.param.items():
        monkeypatch.setenv(name, value)
    user_dir = tmp_path_factory.mktemp("configured-daemon")
    with running_daemon(user_dir) as paths:
        monkeypatch.setenv("TRW_USER_DIR", str(user_dir))
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path / "repo"))
        monkeypatch.delenv("TRW_PROJECT_NAMESPACE", raising=False)
        monkeypatch.setattr(_daemon_store, "_clients", {})
        trw_dir = tmp_path / "repo" / ".trw"
        namespace, client = attach_checkout(trw_dir, MemoryDaemon(paths, user_dir))
        set_current_root(trw_dir.parent)
        reload_config()
        try:
            yield DaemonCheckout(trw_dir, namespace, client)
        finally:
            reload_config()


@pytest.fixture
def fake_memory_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeMemoryStore:
    store = FakeMemoryStore()
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(_store_selection, "selected_store", lambda _trw_dir: (store, FAKE_NAMESPACE))
    return store
