"""Memory-maintenance CLI subcommands (PRD-CORE-231-FR02)."""

from __future__ import annotations

import argparse
import json
import sys


def _run_maintain_verify(args: argparse.Namespace) -> None:
    """Handle ``maintain-verify`` — batch assertion/anchor verification sweep.

    Refreshes stored claim evidence explicitly. Recall reports last-known evidence;
    this command does not establish an automatic schedule or bounded stale-claim
    latency. Batches bound acquisition, not runtime or a concurrent snapshot.
    """

    from trw_mcp.state._store_selection import StoreUnavailableError
    from trw_mcp.tools._maintain_verify import run_maintain_verify_for_project

    try:
        summary = run_maintain_verify_for_project(namespace=getattr(args, "namespace", None))
    except StoreUnavailableError as exc:
        # INC-121 (b): the store being down is expected and its message names the fix -- one line, as export (INC-075).
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    payload = summary.as_dict()
    if bool(getattr(args, "as_json", False)):
        print(json.dumps(payload, indent=2))
        return
    print(
        f"maintain-verify: {payload['entries_processed']} entries, "
        f"{payload['stale_transitions']} newly stale, "
        f"{payload['cleared_transitions']} cleared, "
        f"{payload['persist_failures']} persist failures, "
        f"{payload['entry_failures']} entry failures, "
        f"{payload['invalidated']} verdicts invalidated, "
        f"{payload['tree_behind']} held as unknown (checkout behind the claim) "
        f"({payload['duration_ms']}ms)"
    )


__all__ = ["_run_maintain_verify"]
