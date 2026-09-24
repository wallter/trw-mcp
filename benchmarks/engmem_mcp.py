"""EngMem against the MCP recall path (PRD-CORE-292 measurement harness).

    python trw-mcp/benchmarks/engmem_mcp.py --size 1000 --entry recall --out recall.json
    python trw-mcp/benchmarks/engmem_mcp.py --size 1000 --entry session-start --out session.json

``trw_recall`` does not call ``MemoryClient.recall``: it reaches the backend through
``trw_mcp.state.memory_adapter`` and ranks in ``tools/_recall_impl``. A benchmark of
the library therefore says nothing about what an agent gets from the tool. This
harness replays one EngMem-Synth suite ONCE into one store and scores, at every
query instant, both

* ``trw-hybrid``   -- ``MemoryClient.recall`` (the library path), and
* ``trw-mcp-recall`` -- ``trw_recall`` called through the real FastMCP server
  in-process (middleware, surface resolution and argument coercion included; only
  the stdio transport is skipped),

so any difference is the call path, never the corpus or the cutoff. trw-mcp reads
only a daemon-served store (PRD-CORE-280), so the run starts a real trw-memory daemon
under a throwaway ``TRW_USER_DIR`` (the test suite's ``running_daemon``), pins and
grants the project to a fresh namespace (``attach_checkout``), and the library client
writes and reads that daemon's store file with ``MemoryClient(db_path=...)`` in the
same namespace: both arms read the identical rows. The daemon embeds with the
machine's default model, as the library does, so both arms rank with the same vectors.

Background sync is stubbed out before the server boots: a benchmark must never
send anything anywhere, whatever credentials the machine has.

One size per process: trw-mcp caches its config and backend per process.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "trw-memory"))  # benchmarks.engmem (not part of the trw_memory package)
sys.path.insert(0, str(_REPO / "trw-mcp"))  # tests._memory_daemon / _memory_fixtures: one daemon plumbing

from benchmarks.engmem import synth  # noqa: E402
from benchmarks.engmem.arms import GrepArm, RecencyArm, TrwHybridArm  # noqa: E402
from benchmarks.engmem.replay import ReplayState, replay_and_score  # noqa: E402
from benchmarks.engmem.score import aggregate, latency, metrics_for, paired_mcnemar  # noqa: E402

from tests._memory_daemon import running_daemon  # noqa: E402
from tests._memory_fixtures import MemoryDaemon, attach_checkout  # noqa: E402

PAIRED_METRICS = ("hit@5", "complete@5", "hit@10")
LIBRARY_ARM = "trw-hybrid"


def _per_query(scored: list[Any]) -> list[dict[str, Any]]:
    """Raw per-query outcomes: what a paired, no-per-query-regression gate reads."""
    rows = []
    for s in scored:
        m = metrics_for(s, (5, 10))
        rows.append(
            {
                "qid": s.qid,
                "task": s.task,
                "top10": list(s.ranked[:10]),
                "gold": list(s.gold),
                "forbidden": list(s.forbidden),
                **{k: m[k] for k in ("hit@5", "complete@5", "hit@10", "complete@10", "forbidden@10")},
                "ms": round(s.ms, 1),
            }
        )
    return rows


def _paired(library: list[Any], mcp: list[Any]) -> dict[str, Any]:
    """Library vs MCP on the same queries: exact McNemar plus the regressed qids.

    ``regressed`` (library hit, MCP miss) is the gate's input; the p-value is
    recorded, never read as evidence of parity when it is large.
    """
    lib = {s.qid: metrics_for(s, (5, 10)) for s in library}
    out: dict[str, Any] = {}
    for metric in PAIRED_METRICS:
        stats = paired_mcnemar(library, mcp, metric, ks=(5, 10))
        regressed = sorted(s.qid for s in mcp if lib.get(s.qid, {}).get(metric, 0.0) > metrics_for(s, (5, 10))[metric])
        out[metric] = {**stats, "regressed": regressed}
    return out


class McpRecallArm:
    """``trw_recall`` through the FastMCP server, as an agent calls it."""

    name = "trw-mcp-recall"

    def __init__(self, client: Any) -> None:
        self._client = client
        self.payload_chars: list[int] = []

    async def search(self, query: str, limit: int, as_of: datetime) -> list[tuple[str, int]]:
        result = await self._client.call_tool("trw_recall", {"query": query, "max_results": limit})
        payload = result.structured_content or {}
        self.payload_chars.append(len(json.dumps(payload, default=str)))
        out: list[tuple[str, int]] = []
        for row in payload.get("learnings") or []:
            rid = str(row.get("id") or "")
            out.append((rid, len(str(row.get("summary") or "")) + len(str(row.get("detail") or ""))))
        return out[:limit]


class McpSessionStartArm(McpRecallArm):
    """``trw_session_start(query=...)``: the recall every session begins with.

    Scored like any arm, and its latency and compact payload are the cost side the
    PRD bounds (NFR01). It writes session/run state into the throwaway project,
    which is the tool's normal behaviour and harmless there.
    """

    name = "trw-mcp-session-start"

    async def search(self, query: str, limit: int, as_of: datetime) -> list[tuple[str, int]]:
        result = await self._client.call_tool("trw_session_start", {"query": query})
        payload = result.structured_content or {}
        self.payload_chars.append(len(json.dumps(payload, default=str)))
        return [
            (str(row.get("id") or ""), len(str(row.get("summary") or ""))) for row in (payload.get("learnings") or [])
        ][:limit]


def _p50(values: list[int]) -> int:
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else 0


def _prepare_root(root: Path) -> tuple[Path, Path]:
    """An empty trw-mcp project and an empty daemon home beside it."""
    if root.exists():
        shutil.rmtree(root)  # every run starts from an empty store, or the comparison is a lie
    project, user_dir = root / "project", root / "userhome"
    (project / ".trw").mkdir(parents=True, mode=0o700)
    user_dir.mkdir(mode=0o700)
    return project, user_dir


async def run(size: int, args: argparse.Namespace) -> dict[str, Any]:
    events, queries, successor_of = synth.generate(seed=args.seed, distractors=size)
    project, user_dir = _prepare_root(Path(args.store).expanduser().resolve() / f"n{size}")  # noqa: ASYNC240 - one-shot CLI
    os.environ["TRW_PROJECT_ROOT"] = str(project)
    os.environ["TRW_USER_DIR"] = str(user_dir)
    os.chdir(project)
    with running_daemon(user_dir, keyword_only=False) as paths:
        namespace, _ = attach_checkout(project / ".trw", MemoryDaemon(paths=paths, user_dir=user_dir))
        return await _run_against(size, args, events, queries, successor_of, namespace, paths.store)


async def _run_against(
    size: int,
    args: argparse.Namespace,
    events: Any,
    queries: Any,
    successor_of: Any,
    namespace: str,
    store: Path,
) -> dict[str, Any]:

    from fastmcp import Client
    from trw_memory.client import MemoryClient

    import trw_mcp.server._boot_deferred as boot_deferred

    boot_deferred._resolve_backend_sync = lambda: None  # never sync from a benchmark
    from trw_mcp.server._app import create_app
    from trw_mcp.server._tools import _tool_registrars

    library = MemoryClient(namespace, mode="local", db_path=store)
    server = create_app()
    for register in _tool_registrars():
        register(server)

    mirror = ReplayState(rows=[], successor_of=dict(successor_of), retired=set())
    t0 = time.perf_counter()
    async with Client(server) as mcp_client:
        # One MCP entry point per pass: trw_recall deprioritizes learnings a
        # session already surfaced, so scoring both in one replay makes the second
        # arm measure the first's side effects instead of retrieval.
        mcp_arm = McpSessionStartArm(mcp_client) if args.entry == "session-start" else McpRecallArm(mcp_client)
        arms = [RecencyArm(mirror.rows), GrepArm(mirror.rows), TrwHybridArm(library, namespace), mcp_arm]
        results = await replay_and_score(events, queries, arms, client=library, state=mirror, limit=args.limit)
    wall_s = time.perf_counter() - t0

    out: dict[str, Any] = {}
    for name, scored in results.items():
        agg = aggregate(scored)["ALL"]
        lat = latency(scored)
        out[name] = {
            "hit@10": 100 * agg["hit@10"],
            "complete@10": 100 * agg["complete@10"],
            "forbidden@10": 100 * agg["forbidden@10"],
            "mrr": 100 * agg["mrr"],
            "p50_ms": lat["p50"],
            "p95_ms": lat["p95"],
        }
    out["_per_query"] = {name: _per_query(scored) for name, scored in results.items()}
    out["_paired"] = _paired(results[LIBRARY_ARM], results[mcp_arm.name])
    out["_run"] = {
        "rows": size,
        "queries": len(queries),
        "seed": args.seed,
        "wall_s": wall_s,
        "entry": args.entry,
        "payload_chars_p50": _p50(mcp_arm.payload_chars),
        "payload_chars_p95": sorted(mcp_arm.payload_chars)[int(0.95 * (len(mcp_arm.payload_chars) - 1))]
        if mcp_arm.payload_chars
        else 0,
    }
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--size", type=int, default=1000, help="distractor count (one size per process)")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--store", default="~/.cache/trw-bench/engmem-mcp")
    p.add_argument("--out", default="")
    p.add_argument(
        "--entry",
        choices=("recall", "session-start"),
        default="recall",
        help="which MCP entry point to score against the library (one per pass; see run())",
    )
    args = p.parse_args()
    result = asyncio.run(run(args.size, args))
    for name, m in result.items():
        if name.startswith("_"):
            continue
        print(
            f"{name:16s} hit@10 {m['hit@10']:5.1f}%  complete@10 {m['complete@10']:5.1f}%  "
            f"forbidden@10 {m['forbidden@10']:5.1f}%  mrr {m['mrr']:5.1f}  p50 {m['p50_ms']:7.1f}ms  "
            f"p95 {m['p95_ms']:7.1f}ms"
        )
    print("run:", json.dumps(result["_run"]))
    for metric, stats in result["_paired"].items():
        print(
            f"paired {metric:10s} n={stats['n']} library_only={stats['a_only']} mcp_only={stats['b_only']} "
            f"p={stats['p']:.4f} regressed={stats['regressed']}"
        )
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
