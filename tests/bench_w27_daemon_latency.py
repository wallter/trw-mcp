"""PRD-CORE-302 FR09 / NFR03: the W27 daemon timing script.

Not collected by the suite (the file name does not match ``test_*.py``). Run it
explicitly, once per tree being compared, on the same machine:

    PYTHONPATH=<tree>/trw-mcp/src:<tree>/trw-memory/src \\
      .venv/bin/python -m pytest -q -s -p no:cacheprovider tests/bench_w27_daemon_latency.py

Each test prints one ``W27 <name> <json>`` line; the receipt is
``docs/sprint-mcp7/receipts/w27-daemon-latency.md``. Only public entry points are
used (the MCP tools, ``selected_store``, the daemon client), so the same file
measures a tree from before and after a fix.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from tests._memory_fixtures import DaemonCheckout

_ROWS = 60
_GETS = 200
_WARM_CALLS = 20


def _pcts(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "n": len(ordered),
        "p50_ms": round(statistics.median(ordered) * 1000, 1),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)] * 1000, 1),
    }


def _timed(fn: Callable[[], object], n: int) -> dict[str, float]:
    samples = []
    for _ in range(n):
        started = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - started)
    return _pcts(samples)


def _emit(name: str, payload: dict[str, object]) -> None:
    print(f"W27 {name} " + json.dumps(payload))


@pytest.fixture()
def seeded(daemon_checkout: DaemonCheckout) -> DaemonCheckout:
    from tests._path_isolation import set_current_root

    set_current_root(daemon_checkout.trw_dir.parent)

    async def seed() -> None:
        for index in range(_ROWS):
            await daemon_checkout.client.store(
                f"gotcha {index}: the sqlite migration must pin the driver first {index * 7}", daemon_checkout.namespace
            )

    asyncio.run(seed())
    return daemon_checkout


def test_trw_mcp_store_path(seeded: DaemonCheckout, monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-get latency, first-recall round trips, and warm recall/session_start/learn percentiles."""
    from trw_memory.daemon.client import DaemonClient

    from tests.conftest import extract_tool_fn, make_test_server
    from trw_mcp.state._store_selection import selected_store

    calls: list[str] = []
    real = DaemonClient.call_tool

    async def counting(self: DaemonClient, name: str, arguments: dict[str, object] | None = None) -> object:
        calls.append(name)
        return await real(self, name, arguments)

    monkeypatch.setattr(DaemonClient, "call_tool", counting)
    recall = extract_tool_fn(make_test_server("learning"), "trw_recall")
    started = time.perf_counter()
    recall(query="sqlite migration driver")
    first_ms = round((time.perf_counter() - started) * 1000, 1)
    first_calls = list(calls)
    calls.clear()

    store = selected_store(seeded.trw_dir)[0]
    ids = [row.id for row in store.list_entries(seeded.namespace, limit=_ROWS)]
    get = _timed(lambda: store.get(ids[len(calls) % len(ids)]), _GETS)
    warm_recall = _timed(lambda: recall(query="sqlite migration driver"), _WARM_CALLS)
    session_start = extract_tool_fn(make_test_server("ceremony"), "trw_session_start")
    session_start()
    warm_session_start = _timed(lambda: session_start(), _WARM_CALLS)
    learn = extract_tool_fn(make_test_server("learning"), "trw_learn")
    counter = iter(range(10_000))
    warm_learn = _timed(
        lambda: learn(summary=f"bench learning {next(counter)} about daemon latency", detail="timing row"), _WARM_CALLS
    )
    _emit(
        "store_path",
        {
            "rows": _ROWS,
            "first_trw_recall_ms": first_ms,
            "first_trw_recall_round_trips": len(first_calls),
            "first_trw_recall_tools": first_calls,
            "get": get,
            "trw_recall_warm": warm_recall,
            "trw_session_start_warm": warm_session_start,
            "trw_learn": warm_learn,
        },
    )


def _rss_mb(pid: int) -> float:
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return round(int(out or 0) / 1024, 1)


def test_cold_model_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """First recall from a stopped daemon with the real models, and resident memory after the load."""
    import os

    from trw_memory.daemon import mint_grant
    from trw_memory.daemon._discovery import read_discovery_result
    from trw_memory.daemon.client import DaemonClient

    from tests._memory_daemon import running_daemon

    # The suite points HF_HOME at a per-test home; this measures the real cache.
    monkeypatch.setenv("HF_HOME", os.environ.get("W27_HF_HOME", str(Path("~/.cache/huggingface").expanduser())))
    user_dir = tmp_path / "u"
    namespace = "project:bench"
    with running_daemon(user_dir, keyword_only=False) as paths:
        token = mint_grant(paths, [namespace], root=tmp_path)

        async def seed() -> None:
            client = DaemonClient(token, paths=paths)
            for index in range(40):
                await client.store(f"gotcha {index}: the sqlite migration must pin the driver {index * 13}", namespace)

        asyncio.run(seed())
    # running_daemon kills with SIGKILL, which leaves the record; the next start publishes its own.
    paths.discovery.unlink(missing_ok=True)
    out: dict[str, object] = {"rows": 40}
    with running_daemon(user_dir, keyword_only=False) as paths:
        pid = read_discovery_result(paths).pid  # type: ignore[union-attr]
        out["rss_idle_mb"] = _rss_mb(pid)
        client = DaemonClient(token, paths=paths)

        async def run() -> None:
            started = time.perf_counter()
            await client.recall("sqlite migration driver", namespace)
            out["first_recall_ms"] = round((time.perf_counter() - started) * 1000, 1)
            out["rss_after_model_load_mb"] = _rss_mb(pid)
            samples = []
            for _ in range(_WARM_CALLS):
                started = time.perf_counter()
                await client.recall("sqlite migration driver", namespace)
                samples.append(time.perf_counter() - started)
            out["recall_warm"] = _pcts(samples)

        asyncio.run(run())
    _emit("cold_model", out)
