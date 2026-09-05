"""Memory-maintenance CLI subcommands (PRD-CORE-231-FR02)."""

from __future__ import annotations

import argparse
import json


def _run_clear_shared_anchors(args: argparse.Namespace) -> None:
    """Handle ``maintain-verify --clear-shared-anchors`` (PRD-CORE-267 FR03).

    Reports — and with ``--apply`` clears — anchors on entries whose EXACT
    anchor set is shared by at least ``anchor_shared_set_migration_threshold``
    other entries. Dry-run by default because clearing is irreversible.
    """
    from trw_mcp.tools._anchor_migration import run_anchor_migration_for_project

    summary = run_anchor_migration_for_project(
        apply=bool(getattr(args, "apply", False)),
        namespace=getattr(args, "namespace", None),
    )
    payload = summary.as_dict()
    if bool(getattr(args, "as_json", False)):
        print(json.dumps(payload, indent=2))
        return
    mode = "DRY-RUN — no changes written" if payload["dry_run"] else "APPLIED"
    print(f"clear-shared-anchors — {mode}")
    print(f"  threshold:            {payload['threshold']}")
    print(f"  entries_scanned:      {payload['entries_scanned']}")
    print(f"  sets_over_threshold:  {payload['sets_over_threshold']}")
    print(f"  entries_affected:     {payload['entries_affected']}")
    print(f"  entries_cleared:      {payload['entries_cleared']}")
    print(f"  clear_failures:       {payload['clear_failures']}")
    print(f"  duration_ms:          {payload['duration_ms']}")


def _run_maintain_verify(args: argparse.Namespace) -> None:
    """Handle ``maintain-verify`` — batch assertion/anchor verification sweep.

    Bounds stale-claim latency: recall only verifies entries a query happened to
    return, so entries nobody recalls need this scheduled pass to have their
    ``verification_status`` written through.

    ``--clear-shared-anchors`` selects the PRD-CORE-267 FR03 migration instead:
    it operates on the same memory rows this sweep already re-verifies, which
    is why it lives here rather than in the run-directory collector.
    """
    if bool(getattr(args, "clear_shared_anchors", False)):
        _run_clear_shared_anchors(args)
        return

    from pathlib import Path

    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir
    from trw_mcp.state.memory_adapter import get_backend
    from trw_mcp.tools._maintain_verify import run_maintain_verify

    config = get_config()
    project_root: Path | None
    try:
        project_root = resolve_project_root()
    except Exception:  # justified: fail-open, mirrors verify_assertions(project_root=None)
        project_root = None

    summary = run_maintain_verify(
        get_backend(resolve_trw_dir()),
        assertion_failure_penalty=config.assertion_failure_penalty,
        assertion_stale_threshold_days=config.assertion_stale_threshold_days,
        anchor_validity_verified_floor=config.anchor_validity_verified_floor,
        batch_limit=config.maintain_verify_batch_limit,
        project_root=project_root,
        namespace=getattr(args, "namespace", None),
    )

    payload = summary.as_dict()
    if bool(getattr(args, "as_json", False)):
        print(json.dumps(payload, indent=2))
        return
    print(
        f"maintain-verify: {payload['entries_processed']} entries, "
        f"{payload['stale_transitions']} newly stale, "
        f"{payload['cleared_transitions']} cleared, "
        f"{payload['persist_failures']} persist failures "
        f"({payload['duration_ms']}ms)"
    )


__all__ = ["_run_clear_shared_anchors", "_run_maintain_verify"]
