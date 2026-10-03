#!/usr/bin/env python3
"""Measure the sync cycle (SYNC-PERF-COST-SOAK b): ONE JSON object on stdout, no pytest, scratch everything.

    <canary venv>/bin/python sync_soak_probe.py [--cycles 12] [--rows 20]

It starts a loopback stub of the two backend endpoints a cycle uses (POST /v1/sync/learnings, GET /v1/intel/state),
a scratch HOME / TRW_USER_DIR / project and a scratch memory daemon, and runs real push and pull halves of
`BackendSyncClient` against the stub. The environment is scrubbed first, so it never reaches the live daemon, the live
store or a real key (the key is a dummy). Each cycle seeds `--rows` new learnings, then times the push half and the pull
half separately.

Output keys (per-cycle lists, or null with a reason in `not_measured`; never 0 for "unknown"):
  push_ms, pull_ms, pushed_rows, pulled_rows, pushed_bytes, pulled_bytes, rejected_count, cycles_n, not_measured
Exit 0 once the JSON is printed, 2 on a setup failure (reason on stderr).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DUMMY_KEY = "trw_e2e_dummy_not_a_real_key"


class Stub:
    """The two endpoints, recording what crossed the wire."""

    def __init__(self, rows_per_pull: int) -> None:
        self.rows_per_pull = rows_per_pull
        self.push_bytes: list[int] = []
        self.push_rows: list[int] = []
        self.pull_bytes: list[int] = []
        self.pull_rows: list[int] = []
        self.rejected = 0
        self.seq = 0
        self.lock = threading.Lock()


def _handler(stub: Stub) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:  # quiet
            return

        def _send(self, status: int, body: dict[str, object]) -> int:
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return len(raw)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            entries = payload.get("entries", []) if isinstance(payload, dict) else payload
            with stub.lock:
                stub.push_bytes.append(length)
                stub.push_rows.append(len(entries))
            self._send(200, {"errors": 0, "inserted": len(entries), "updated": 0, "skipped": 0})

        def do_GET(self) -> None:
            with stub.lock:
                start = stub.seq
                stub.seq += stub.rows_per_pull
                rows = [
                    {
                        "source_learning_id": f"stub-{n}",
                        "summary": f"stub learning {n}",
                        "detail": "x" * 200,
                        "impact": 0.5,
                        "tags": ["soak"],
                        "type": "pattern",
                        "status": "active",
                        "sync_seq": n + 1,
                        "vector_clock": {"stub": 1},
                        "metadata": {},
                    }
                    for n in range(start, start + stub.rows_per_pull)
                ]
                body = {
                    "etag": f"soak-{stub.seq}",
                    "sync_hints": {"next_poll_recommended_at": "2999-01-01T00:00:00Z", "polling_cap_seconds": 300},
                    "team_learnings": rows,
                    "next_seq": stub.seq,
                }
                sent = self._send(200, body)
                stub.pull_bytes.append(sent)
                stub.pull_rows.append(len(rows))

    return Handler


def _scrub_environment(root: Path, project: Path) -> None:
    """Nothing inherited can point this process at the live daemon, store or key."""
    keep = {key: os.environ[key] for key in ("PATH", "LANG", "SYSTEMROOT") if key in os.environ}
    os.environ.clear()
    os.environ.update(keep)
    home = root / "home"
    (home / ".trw").mkdir(parents=True)
    tmp = root / "tmp"
    tmp.mkdir()
    os.environ.update(
        {
            "HOME": str(home),
            "TRW_USER_DIR": str(home / ".trw"),
            "TMPDIR": str(tmp),
            "TRW_PROJECT_ROOT": str(project),
            "MEMORY_DAEMON_AUTOSTART": "true",
            "MEMORY_EMBEDDINGS_ENABLED": "false",
            "TRW_INSTALL_EMBEDDINGS": "false",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )


def _write_project(project: Path, url: str) -> None:
    trw = project / ".trw"
    trw.mkdir(parents=True)
    (trw / "config.yaml").write_text(
        "installation_id: sync-soak\n"
        "project_namespace: project:sync-soak-0a1b2c3d\n"
        f'platform_urls: ["{url}"]\n'
        "learning_sharing_enabled: true\n"
        "team_sync_enabled: true\n"
        "platform_telemetry_enabled: false\n",
        encoding="utf-8",
    )
    cred = trw / "credentials.yaml"
    fd = os.open(cred, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, f'platform_api_key: "{DUMMY_KEY}"\n'.encode())
    os.close(fd)


def _percentile_free(values: list[float]) -> list[float]:
    return [round(v, 2) for v in values]


async def _run(project: Path, cycles: int, rows: int, stub: Stub) -> dict[str, object]:
    from trw_memory.daemon import DaemonPaths, mint_grant, write_checkout_grant

    from trw_mcp.models.config import get_config
    from trw_mcp.state._store_selection import selected_store
    from trw_mcp.sync.client import BackendSyncClient

    trw_dir = project / ".trw"
    # The scratch checkout's own daemon grant, minted into the scratch TRW_USER_DIR (as `trw-mcp memory token` does).
    namespace = "project:sync-soak-0a1b2c3d"
    write_checkout_grant(trw_dir, mint_grant(DaemonPaths.resolve(), {namespace, "user:local"}, root=project))
    config = get_config()
    client = BackendSyncClient(config=config, trw_dir=trw_dir)
    if not client._targets:
        raise RuntimeError("no sync target resolved from the scratch config")
    store, namespace = selected_store(trw_dir)

    push_ms: list[float] = []
    pull_ms: list[float] = []
    pushed_rows: list[int] = []
    pulled_rows: list[int] = []
    pushed_bytes: list[int] = []
    pulled_bytes: list[int] = []
    for cycle in range(cycles):
        for n in range(rows):
            store.put(
                f"soak learning {cycle}-{n}",
                namespace,
                {"detail": "soak detail " + "y" * 200, "tags": ["soak"], "importance": 0.6, "source": "agent"},
            )
        before_rows, before_bytes = sum(stub.push_rows), sum(stub.push_bytes)
        started = time.perf_counter()
        push_outcome = await client._run_one_cycle(force=True, push=True, pull=False)
        push_ms.append((time.perf_counter() - started) * 1000)
        if push_outcome != "ok":
            raise RuntimeError(f"push half answered {push_outcome!r}")
        pushed_rows.append(sum(stub.push_rows) - before_rows)
        pushed_bytes.append(sum(stub.push_bytes) - before_bytes)

        before_rows, before_bytes = sum(stub.pull_rows), sum(stub.pull_bytes)
        started = time.perf_counter()
        pull_outcome = await client._run_one_cycle(force=True, push=False, pull=True)
        pull_ms.append((time.perf_counter() - started) * 1000)
        if pull_outcome != "ok":
            raise RuntimeError(f"pull half answered {pull_outcome!r}")
        pulled_rows.append(sum(stub.pull_rows) - before_rows)
        pulled_bytes.append(sum(stub.pull_bytes) - before_bytes)

    return {
        "push_ms": _percentile_free(push_ms),
        "pull_ms": _percentile_free(pull_ms),
        "pushed_rows": pushed_rows,
        "pulled_rows": pulled_rows,
        "pushed_bytes": pushed_bytes,
        "pulled_bytes": pulled_bytes,
        "rejected_count": stub.rejected,
        "cycles_n": cycles,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cycles", type=int, default=12)
    parser.add_argument("--rows", type=int, default=20)
    args = parser.parse_args()

    root = Path(tempfile.mkdtemp(prefix="trw-sync-soak-"))
    stub = Stub(rows_per_pull=args.rows)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(stub))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    project = root / "proj"
    try:
        _scrub_environment(root, project)
        _write_project(project, f"http://127.0.0.1:{server.server_address[1]}")
        os.chdir(project)
        result = asyncio.run(_run(project, args.cycles, args.rows, stub))
        result["not_measured"] = {"lambda_duration_ms": "needs a live-backend probe; this one is scratch-only"}
        print(json.dumps(result))
        return 0
    except Exception as exc:  # report the reason, never a fabricated number
        print(f"sync_soak_probe: setup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        server.shutdown()
        _reap_daemon(root)
        shutil.rmtree(root, ignore_errors=True)


def _reap_daemon(root: Path) -> None:
    """Stop the scratch daemon this probe started (its discovery record is under the scratch TRW_USER_DIR)."""
    try:
        from trw_memory.daemon import DaemonPaths

        info = json.loads(DaemonPaths.resolve().discovery.read_text())
        pid = int(info["pid"])
    except Exception:  # trw-fail-silent-allow: best-effort cleanup of the probe's own scratch daemon after its JSON is printed; no daemon, or one already gone
        return
    if str(root) in str(Path(os.environ.get("TRW_USER_DIR", ""))):
        try:
            os.kill(pid, 15)
        except OSError:  # trw-fail-silent-allow: the scratch daemon already exited; cleanup only
            pass


if __name__ == "__main__":
    sys.exit(main())
