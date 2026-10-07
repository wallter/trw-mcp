"""``trw-mcp formation status`` rendering, split from :mod:`trw_mcp.tools._formation_cli` (read-only roll-up)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # import-time cost is paid only by the type checker
    from trw_mcp.formation import FormationStatus

_HEADERS = ("member", "client", "role", "status", "phase", "build", "review", "delivery", "stale")


def run_status(args: argparse.Namespace, run_path: Path) -> None:
    """``formation status``: the read-only member roll-up, as a table or (``--json``) with the usage ledger."""
    from trw_mcp.formation import FormationError, pause_roll_call, status

    board = status(run_path=run_path)
    if board is None:
        raise FormationError("no formation is active for this run")
    pause = pause_roll_call(Path(board.manifest_path))
    if getattr(args, "as_json", False):
        from trw_mcp.formation import formation_usage, member_usage

        # PRD-CORE-290-FR01: the usage ledger lives on this CLI surface only (NFR01),
        # and its total sits beside the outcome checks for the same span (NFR02).
        runs = [Path(row.run_path) for row in board.rows if row.run_path]
        members = []
        for row in board.rows:
            usage = member_usage(Path(row.run_path)) if row.run_path else {}
            members.append({**row.as_dict(), **({"usage": usage} if usage else {})})
        outcomes = {
            "members": len(board.rows),
            "builds_passed": sum(row.build == "passed" for row in board.rows),
            "reviews_open": sum(row.review not in ("", "pass", "passed") for row in board.rows),
        }
        print(
            json.dumps(
                {
                    "formation_id": board.formation_id,
                    "revision": board.revision,
                    "manifest_path": board.manifest_path,
                    "members": members,
                    "usage": {**formation_usage(runs), "outcomes": outcomes},
                    "non_terminal": [{"member_id": m, "status": s} for m, s in board.non_terminal],
                    "stalls": [finding.as_dict() for finding in board.stalls],
                    "stall_measurement": board.stall_measurement,
                    "stall_scope": board.stall_scope,
                    **({"pause": pause} if pause is not None else {}),
                },
                indent=2,
            )
        )
        return
    print(f"formation {board.formation_id} (revision {board.revision}) — {board.manifest_path}")
    print(_render_table(board))
    for member_id, member_status in board.non_terminal:
        print(f"  waiting on {member_id}: {member_status}")
    for finding in board.stalls:
        print(f"  {finding.line()}")
    if board.stall_measurement != "measured":
        for source in ("mailbox", "mcp_tool_calls"):
            if board.stall_scope[source] != "measured":
                print(f"  stalls not_measured: {source} unavailable")
    if pause is not None:
        acked, missing = list(pause["acked"]), list(pause["not_acked"])  # type: ignore[call-overload]
        overdue = " OVERDUE" if pause.get("overdue") else ""
        print(f"PAUSED {pause['pause_id']} since {pause['since_utc']}{overdue}: {pause['reason']}")
        print(f"  acked {len(acked)}/{len(acked) + len(missing)}; not acked: {', '.join(missing) or 'none'}")


def _render_table(board: FormationStatus) -> str:
    rows = [
        [
            row.member_id,
            row.client,
            row.role,
            row.status,
            row.phase,
            row.build,
            row.review,
            row.delivery,
            row.stale_reason if row.stale else "",
        ]
        for row in board.rows
    ]
    widths = [
        max(len(_HEADERS[i]), *(len(r[i]) for r in rows)) if rows else len(_HEADERS[i]) for i in range(len(_HEADERS))
    ]
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(_HEADERS))]
    lines.append("  ".join("-" * widths[i] for i in range(len(_HEADERS))))
    lines.extend("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in rows)
    return "\n".join(lines)
