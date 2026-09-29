"""r8 ENGMEM-HARNESS-LOCK-RACE: the direct client must open only after the
daemon has opened and migrated the store, never before or concurrently.

``engmem_mcp.py`` starts a real trw-memory daemon against a fresh store and
then, separately, opens a direct ``MemoryClient`` on that same file. Both
opens migrate a fresh (0-byte) store under the EXCLUSIVE ``migrate`` hold
(``trw_memory/_store_lock.py``, a fixed 30s wait); under host contention the
harness lost that race 5/5 at size 1000 (r8 heavy-slot evidence,
``scratchpad/r8/heavy/RESULTS.md``) with ``StoreBusyError``, before any query
ran.

Publishing the daemon's discovery file (what ``running_daemon`` already waits
for) only proves its socket is bound (``daemon/_serve.py``'s docstring); the
store itself opens lazily, on the daemon's first real call. So
``engmem_mcp._open_single_writer`` makes a real round trip through the daemon
(``memory_status`` -- a positive readiness signal, not a sleep or a bumped
timeout) and waits for it to finish BEFORE constructing the direct
``MemoryClient`` that opens the same file -- sequencing the two opens rather
than making the harness single-writer (both still end up with the store open
afterwards). This module's first two tests prove that ordering directly at
the seam with fakes; ``test_public_entry_orders_daemon_migration_before_direct_open``
below drives the harness's actual public entry point (``run()``) through
BOTH the pre-fix (``git show 16b9d3e30``) and the fixed module, patching only
the shared source-module functions both versions import (never the harness's
own aliases), and asserts on the recorded ORDER of events -- not on whether a
name exists -- so it fails on BEHAVIOUR at the pre-fix commit.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

_MODULE_PATH = Path(__file__).resolve().parents[1] / "benchmarks" / "engmem_mcp.py"
_spec = importlib.util.spec_from_file_location("engmem_mcp", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
engmem_mcp = importlib.util.module_from_spec(_spec)
sys.modules["engmem_mcp"] = engmem_mcp
_spec.loader.exec_module(engmem_mcp)


class _FakeDaemonClient:
    """Stands in for ``DaemonClient``: no HTTP, no daemon process."""

    def __init__(self, namespace: str) -> None:
        self.namespace = namespace


class _FakePaths:
    """Stands in for ``DaemonPaths``: just the one attribute ``_open_single_writer`` reads."""

    def __init__(self, store: Path) -> None:
        self.store = store


def _fake_attach_checkout(order: list[str]) -> Any:
    def _attach(trw_dir: Path, daemon: Any) -> tuple[str, _FakeDaemonClient]:
        order.append("attach_checkout")
        return "project:fake-ns", _FakeDaemonClient("project:fake-ns")

    return _attach


def _fake_await_ready(order: list[str], *, fail: bool = False) -> Any:
    async def _ready(client: _FakeDaemonClient, namespace: str) -> None:
        assert isinstance(client, _FakeDaemonClient)
        assert namespace == "project:fake-ns"
        order.append("await_ready:start")
        if fail:
            order.append("await_ready:raised")
            raise RuntimeError("daemon never opened its store")
        order.append("await_ready:done")

    return _ready


def _fake_memory_client_factory(order: list[str]) -> Any:
    def _factory(namespace: str, *, mode: str, db_path: Path) -> str:
        order.append("direct_open")
        return f"library-client:{namespace}:{mode}:{db_path}"

    return _factory


@pytest.mark.asyncio
async def test_direct_client_opens_only_after_daemon_readiness_completes(tmp_path: Path) -> None:
    """The direct MemoryClient must open strictly after ``await_ready`` returns, never before or during it."""
    order: list[str] = []
    project = tmp_path / "project"
    user_dir = tmp_path / "userhome"
    project.mkdir()
    user_dir.mkdir()
    paths = _FakePaths(user_dir / "memory" / "memory.db")

    namespace, store, library = await engmem_mcp._open_single_writer(
        project,
        user_dir,
        paths,
        attach_checkout=_fake_attach_checkout(order),
        await_ready=_fake_await_ready(order),
        memory_client_factory=_fake_memory_client_factory(order),
    )

    assert order == ["attach_checkout", "await_ready:start", "await_ready:done", "direct_open"]
    assert namespace == "project:fake-ns"
    assert store == paths.store
    assert library == f"library-client:project:fake-ns:local:{paths.store}"


@pytest.mark.asyncio
async def test_direct_client_never_opens_when_daemon_readiness_fails(tmp_path: Path) -> None:
    """If the daemon never confirms its store is open, the harness must not fall back to a second writer."""
    order: list[str] = []
    project = tmp_path / "project"
    user_dir = tmp_path / "userhome"
    project.mkdir()
    user_dir.mkdir()
    paths = _FakePaths(user_dir / "memory" / "memory.db")

    with pytest.raises(RuntimeError, match="daemon never opened its store"):
        await engmem_mcp._open_single_writer(
            project,
            user_dir,
            paths,
            attach_checkout=_fake_attach_checkout(order),
            await_ready=_fake_await_ready(order, fail=True),
            memory_client_factory=_fake_memory_client_factory(order),
        )

    assert order == ["attach_checkout", "await_ready:start", "await_ready:raised"]
    assert "direct_open" not in order


def test_production_defaults_are_the_real_callables() -> None:
    """The seam's defaults are the real production functions, so the production path is unchanged."""
    defaults = engmem_mcp._open_single_writer.__kwdefaults__
    assert defaults["attach_checkout"] is engmem_mcp._real_attach_checkout
    assert defaults["await_ready"] is engmem_mcp._await_daemon_store_ready
    assert defaults["memory_client_factory"] is engmem_mcp._RealMemoryClient


# --- run()-level proof: patch the SHARED source modules, not the harness's own aliases -------
#
# The three fixtures above call ``_open_single_writer`` directly, so they only prove the seam
# is wired correctly. They cannot fail on the pre-fix commit on BEHAVIOUR -- ``_open_single_writer``
# does not exist there, so calling it is an ``AttributeError`` on a missing name, not evidence the
# harness actually raced. The test below instead drives ``run()`` -- the same public entry ``main()``
# calls, present in both versions -- and patches only ``tests._memory_daemon.running_daemon``,
# ``tests._memory_fixtures.attach_checkout`` and ``trw_memory.client.MemoryClient``: the THREE
# source-module functions BOTH the pre-fix and the fixed ``engmem_mcp.py`` import (under whatever
# local alias each version happens to use). A shared ``order`` list records "daemon_started" ->
# "attach_checkout" -> ("daemon_store_migrated" only if something calls the fake daemon's
# ``status()``) -> "direct_client_opened". The fake daemon's store migrates lazily, on the FIRST
# ``status()`` call, exactly as the real daemon opens its store lazily on its first real request
# -- so "daemon_store_migrated" appears in the order only when the harness actually asked the
# daemon whether it was ready, which the pre-fix harness never did.


class _RunLevelFakePaths:
    def __init__(self, store: Path) -> None:
        self.store = store


class _RunLevelFakeBackend:
    def update(self, *args: Any, **kwargs: Any) -> None:
        return None


class _RunLevelFakeDaemonClient:
    def __init__(self, order: list[str], state: dict[str, bool]) -> None:
        self._order = order
        self._state = state

    async def status(self, namespace: str) -> dict[str, object]:
        # The real daemon opens (and migrates, if needed) its store lazily, on its first real
        # call -- never earlier. This mirrors that: the FIRST status() call is what "migrates".
        if not self._state["migrated"]:
            self._state["migrated"] = True
            self._order.append("daemon_store_migrated")
        return {"total_entries": 0}


def _make_run_level_fakes(order: list[str]) -> tuple[Any, Any, Any]:
    state = {"migrated": False}

    @contextmanager
    def _running_daemon(user_dir: Path, *, keyword_only: bool = True) -> Iterator[_RunLevelFakePaths]:
        order.append("daemon_started")
        yield _RunLevelFakePaths(Path(user_dir) / "memory" / "memory.db")

    def _attach_checkout(trw_dir: Path, daemon: Any) -> tuple[str, _RunLevelFakeDaemonClient]:
        order.append("attach_checkout")
        return "project:fake-ns", _RunLevelFakeDaemonClient(order, state)

    class _FakeMemoryClient:
        def __init__(self, namespace: str, *, mode: str, db_path: Path) -> None:
            order.append("direct_client_opened")
            self.namespace = namespace

        def _get_backend(self) -> _RunLevelFakeBackend:
            return _RunLevelFakeBackend()

        async def recall(self, *args: Any, **kwargs: Any) -> list[Any]:
            return []

        async def bulk_store(self, *args: Any, **kwargs: Any) -> list[Any]:
            return []

    return _running_daemon, _attach_checkout, _FakeMemoryClient


def _exec_module_at(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


async def _drive_run_and_record_order(
    path: Path, module_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> list[str]:
    """Patch the three shared source functions, load *path* fresh, and run its ``run()``.

    Patching happens BEFORE the module is (re-)loaded: a version that binds its own
    module-level alias (``from tests._memory_daemon import running_daemon as
    _real_running_daemon``, the fixed harness's pattern) copies the current attribute value
    at import time, so the patch must land first for either version to pick it up.
    """
    import trw_memory.client as _mc

    import tests._memory_daemon as _md
    import tests._memory_fixtures as _mf

    order: list[str] = []
    running_daemon, attach_checkout, fake_client_cls = _make_run_level_fakes(order)
    monkeypatch.setattr(_md, "running_daemon", running_daemon)
    monkeypatch.setattr(_mf, "attach_checkout", attach_checkout)
    monkeypatch.setattr(_mc, "MemoryClient", fake_client_cls)

    mod = _exec_module_at(path, module_name)
    args = argparse.Namespace(size=0, seed=1, limit=5, store=str(tmp_path / "store"), entry="recall")

    original_cwd = Path.cwd()
    original_env = {k: os.environ.get(k) for k in ("TRW_PROJECT_ROOT", "TRW_USER_DIR")}
    try:
        # The real MCP tool call inside _run_against reaches for a real daemon this test never
        # starts, so it fails fast (no discovery record under this fake TRW_USER_DIR) rather than
        # hang; a bound is still kept in case that ever changes.
        await asyncio.wait_for(mod.run(0, args), timeout=20.0)
    except Exception:  # trw-fail-silent-allow: only the ORDER recorded up to this point is under
        pass  # test; both versions are expected to fail past that point (no real daemon/model here)
    finally:
        os.chdir(original_cwd)
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return order


def _assert_daemon_migrates_before_direct_open(order: list[str]) -> None:
    assert "daemon_store_migrated" in order, (
        "the daemon never confirmed, via a real call, that it had opened and migrated its "
        f"store before the harness proceeded: {order}"
    )
    assert order.index("daemon_store_migrated") < order.index("direct_client_opened"), (
        f"the direct client opened before (or without waiting for) the daemon's store open+migrate to finish: {order}"
    )


@pytest.mark.asyncio
async def test_public_entry_orders_daemon_migration_before_direct_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``run()`` (the entry ``main()`` calls) must open the direct client only after the daemon
    has confirmed -- via a real round trip -- that it opened and migrated the store."""
    order = await _drive_run_and_record_order(_MODULE_PATH, "engmem_mcp_runlevel_fixed", tmp_path, monkeypatch)
    _assert_daemon_migrates_before_direct_open(order)
