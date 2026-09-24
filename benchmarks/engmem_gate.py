"""PRD-CORE-292 release gate over engmem_mcp.py results (FR02 + FR06).

    python trw-mcp/benchmarks/engmem_gate.py \\
        --baseline trw-mcp/benchmarks/baselines/core292-pre --candidate <dir> \\
        --sizes 1000,5000,20000 --entries recall,session-start

Reads ``base-<entry>-<size>.json`` from both directories (the names
``engmem_mcp.py --out`` is given by the release runbook) and FAILS -- exit 1 with
every reason listed -- on any of:

* a forbidden row in the MCP entry point's top 10, on any query;
* a per-query complete@10 regression of the MCP entry point against the frozen
  pre-change baseline;
* a per-query hit@5 / complete@5 / hit@10 regression of the MCP entry point
  against the library on the SAME run (FR02; the McNemar p-value is recorded by
  the harness and never read here as evidence of parity);

Each entry point is judged at the depth it shows. ``trw_session_start`` renders
at most ``SESSION_MAX_STUBS`` stubs (PRD-CORE-294 FR02), so it is paired with the
library's top ``SESSION_MAX_STUBS`` and held to that depth against the baseline:
a gold row the library ranks below that line is not one session start may show.
Outcomes are read from each row's ``top10`` and ``gold``, so a short list fails.
* a missing file, zero executed queries, no positive control (the library found
  nothing), or an MCP entry point that returned nothing for every query.

A skipped or absent measurement is a failure, never a 0% leak.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from trw_mcp.tools._recall_presenter import SESSION_MAX_STUBS

LIBRARY_ARM = "trw-hybrid"
MCP_ARMS = {"recall": "trw-mcp-recall", "session-start": "trw-mcp-session-start"}
# Per entry point: the outcomes paired against the library, then the one held against the baseline.
JUDGED = {
    "recall": (("hit@5", "complete@5", "hit@10"), "complete@10"),
    "session-start": ((f"hit@{SESSION_MAX_STUBS}", f"complete@{SESSION_MAX_STUBS}"), f"complete@{SESSION_MAX_STUBS}"),
}


def _rows(result: dict[str, Any], arm: str) -> dict[str, dict[str, Any]]:
    return {row["qid"]: row for row in result.get("_per_query", {}).get(arm, [])}


def _outcome(row: dict[str, Any], metric: str) -> bool:
    """``hit@k`` or ``complete@k`` of one query, from the ids it returned."""
    kind, depth = metric.split("@")
    shown, gold = set(row["top10"][: int(depth)]), set(row["gold"])
    return bool(shown & gold) if kind == "hit" else gold <= shown


def check(baseline: dict[str, Any], candidate: dict[str, Any], entry: str) -> list[str]:
    """Every reason ``candidate`` fails the gate for one entry point at one size."""
    arm, (paired, held) = MCP_ARMS[entry], JUDGED[entry]
    mcp, library, before = _rows(candidate, arm), _rows(candidate, LIBRARY_ARM), _rows(baseline, arm)
    reasons: list[str] = []
    if not mcp:
        return [f"{arm}: no executed queries"]
    if not any(_outcome(row, "hit@10") for row in library.values()):
        reasons.append(f"{LIBRARY_ARM}: no positive control (0 hits) -- the fixture measured nothing")
    if not any(row["top10"] for row in mcp.values()):
        reasons.append(f"{arm}: every query returned nothing")
    for qid, row in sorted(mcp.items()):
        if row["forbidden@10"]:
            leaked = sorted(set(row["top10"]) & set(row["forbidden"]))
            reasons.append(f"{arm} {qid}: forbidden row(s) in top 10: {leaked}")
        if qid in before and _outcome(before[qid], held) > _outcome(row, held):
            reasons.append(f"{arm} {qid}: {held} regressed against the frozen baseline")
        lib = library.get(qid)
        if lib is None:
            reasons.append(f"{arm} {qid}: no library outcome to pair with")
            continue
        reasons.extend(
            f"{arm} {qid}: {metric} regressed against the library on the same run"
            for metric in paired
            if _outcome(lib, metric) > _outcome(row, metric)
        )
    missing = sorted(set(before) - set(mcp))
    if missing:
        reasons.append(f"{arm}: queries in the baseline but not executed: {missing}")
    return reasons


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--baseline", required=True)
    p.add_argument("--candidate", required=True)
    p.add_argument("--sizes", default="1000,5000,20000")
    p.add_argument("--entries", default="recall,session-start")
    args = p.parse_args()
    failures: list[str] = []
    for entry in args.entries.split(","):
        for size in args.sizes.split(","):
            name = f"base-{entry}-{size}.json"
            base_path, cand_path = Path(args.baseline) / name, Path(args.candidate) / name
            if not base_path.is_file() or not cand_path.is_file():
                failures.append(f"{name}: missing ({'baseline' if not base_path.is_file() else 'candidate'})")
                continue
            reasons = check(json.loads(base_path.read_text()), json.loads(cand_path.read_text()), entry)
            failures.extend(f"{name}: {reason}" for reason in reasons)
    for line in failures:
        print(f"FAIL {line}")
    print("PASS" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
