"""2026-09-25 P0: a real-model daemon survives concurrent recall and store.

The operator's daemon aborted in Metal when two threads encoded on the MPS device
at once. The fix pins every model to CPU on macOS
(``trw_memory.embeddings.local.INFERENCE_DEVICE``);
``trw-memory/tests/test_daemon_crash_recovery.py`` pins that invariant. This bench
is the end-to-end check: one daemon with the real embedder and cross-encoder, and
several threads each on their own event loop and client, recalling and storing at
once. The daemon must still be the same live process afterwards, and every call
must answer. Not collected by the suite:

    PYTHONPATH=<tree>/trw-mcp/src:<tree>/trw-memory/src \\
      .venv/bin/python -m pytest -q -s -p no:cacheprovider tests/bench_p0_daemon_concurrency.py
"""

from __future__ import annotations

import asyncio
import json
import os
import pwd
import threading
from pathlib import Path

import pytest

_THREADS = 8
_ROUNDS = 12


def test_a_real_model_daemon_survives_parallel_recall_and_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_memory.daemon import mint_grant
    from trw_memory.daemon._discovery import read_discovery_result
    from trw_memory.daemon.client import DaemonClient
    from trw_memory.storage._pid_liveness import _pid_is_live

    from tests._memory_daemon import running_daemon

    real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    monkeypatch.setenv("HF_HOME", str(real_home / ".cache" / "huggingface"))
    monkeypatch.setenv("MEMORY_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    user_dir = tmp_path / "user"
    namespace = "project:hammer"
    errors: list[str] = []
    answered = [0]
    lock = threading.Lock()

    with running_daemon(user_dir, keyword_only=False) as paths:
        pid = read_discovery_result(paths).pid  # type: ignore[union-attr]
        token = mint_grant(paths, [namespace], root=tmp_path)

        async def seed() -> None:
            client = DaemonClient(token, paths=paths)
            for index in range(40):
                await client.store(f"gotcha {index}: pin the sqlite driver before migration {index * 3}", namespace)

        asyncio.run(seed())

        def worker(index: int) -> None:
            async def run() -> None:
                client = DaemonClient(token, paths=paths)
                for round_ in range(_ROUNDS):
                    try:
                        if (index + round_) % 3 == 0:
                            await client.store(
                                f"thread {index} round {round_}: rotate keys every {round_} days", namespace
                            )
                        else:
                            result = await client.recall("sqlite migration driver ordering", namespace)
                            if "dense" in result:
                                raise AssertionError(f"dense unavailable: {result['dense']}")
                        with lock:
                            answered[0] += 1
                    except Exception as exc:  # the bench reports every failure, then asserts none
                        with lock:
                            errors.append(f"{type(exc).__name__}: {exc}"[:300])

            asyncio.run(run())

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(_THREADS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        still_live = _pid_is_live(pid, paths.lock)
        same_daemon = read_discovery_result(paths).pid == pid  # type: ignore[union-attr]

    out = {"threads": _THREADS, "calls": _THREADS * _ROUNDS, "answered": answered[0], "errors": errors[:5]}
    out.update({"daemon_still_live": still_live, "same_daemon": same_daemon})
    print("P0 " + json.dumps(out))
    assert still_live and same_daemon
    assert errors == []
    assert answered[0] == _THREADS * _ROUNDS
