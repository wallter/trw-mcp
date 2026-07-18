"""Garbage-collection CLI subcommand."""

from __future__ import annotations

import argparse
import json
import sys


def _run_gc(args: argparse.Namespace) -> None:
    """Handle the ``gc`` subcommand — stale-run sweep (PRD-CORE-141 FR11).

    Defaults come from the current :class:`TRWConfig` for any flag not
    explicitly provided.  ``TRW_SESSION_ID`` is inherited from the parent
    environment — the subcommand does not override it.
    """
    import time as _time
    from dataclasses import asdict
    from datetime import datetime as _datetime
    from datetime import timezone as _timezone
    from pathlib import Path as _Path

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state._paths import resolve_project_root
    from trw_mcp.state._pin_store import load_pin_store
    from trw_mcp.state._run_gc import sweep_stale_runs

    config = TRWConfig()
    staleness_hours = args.staleness_hours if args.staleness_hours is not None else config.run_staleness_hours
    grace_hours = args.grace_hours if args.grace_hours is not None else config.run_staleness_grace_hours
    dry_run = bool(getattr(args, "dry_run", True))
    as_json = bool(getattr(args, "as_json", False))

    project_root = resolve_project_root()
    runs_root = project_root / config.runs_root

    if not runs_root.is_dir():
        msg = f"runs_root not found: {runs_root}"
        if as_json:
            print(json.dumps({"error": msg}, indent=2))
        else:
            print(msg, file=sys.stderr)
        sys.exit(1)

    # Assemble live-pinned paths — pins whose heartbeat is within pin_ttl_hours.
    # Creator PIDs are diagnostic because pins survive MCP restarts;
    # load_pin_store already applies stale-path eviction.
    pin_ttl_seconds = config.pin_ttl_hours * 3600
    now = _time.time()
    pinned_paths: list[_Path] = []
    for entry in load_pin_store().values():
        run_path_raw = entry.get("run_path")
        heartbeat_raw = entry.get("last_heartbeat_ts")
        if not isinstance(run_path_raw, str):
            continue
        if isinstance(heartbeat_raw, str):
            try:
                # Accept the same ISO8601-with-Z form the pin store writes.
                hb_ts = heartbeat_raw.rstrip("Z")
                hb_dt = _datetime.fromisoformat(hb_ts)
                hb_unix = hb_dt.replace(tzinfo=_timezone.utc).timestamp()
                if now - hb_unix > pin_ttl_seconds:
                    continue
            except ValueError:
                # Malformed heartbeat — be conservative and keep the pin in
                # the live set so we do not accidentally abandon an active run.
                pass
        pinned_paths.append(_Path(run_path_raw))

    report = sweep_stale_runs(
        runs_root,
        staleness_hours,
        grace_hours,
        pinned_paths,
        dry_run=dry_run,
    )

    # PRD-CORE-219-FR05: candidate-ref retention rides the same gc sweep.
    # Checkpoint-referenced transactions retain through run archival.
    candidate_report: dict[str, list[str]] = {"collected": [], "retained": []}
    if not dry_run:
        from trw_mcp.state.git_commit_transaction import cleanup_candidates

        referenced: set[str] = set()
        # Recursive ``**`` glob (not ``*/*``) so FLAT and OLD_NESTED run
        # layouts are scanned too — the PROPER-layout-only ``*/*`` glob would
        # miss their checkpoints and fail to protect their referenced
        # candidates from collection (documented iter_run_dirs gotcha).
        for checkpoint_file in runs_root.glob("**/meta/checkpoints.jsonl"):
            try:
                for line in checkpoint_file.read_text(encoding="utf-8").splitlines():
                    if '"candidate_evidence"' not in line:
                        continue
                    record = json.loads(line)
                    evidence = record.get("candidate_evidence", {})
                    if isinstance(evidence, dict) and evidence.get("transaction_id"):
                        referenced.add(str(evidence["transaction_id"]))
            except (OSError, ValueError):
                continue  # unreadable journal never widens collection
        candidate_report = cleanup_candidates(
            project_root,
            now_epoch_days=int(now // 86400),
            referenced_transaction_ids=frozenset(referenced),
        )

    if as_json:
        payload = asdict(report)
        payload["candidate_refs"] = candidate_report
        print(json.dumps(payload, indent=2))
    else:
        header = "DRY-RUN — no changes written" if dry_run else "SWEEP COMPLETE"
        print(f"trw-mcp gc — {header}")
        print(f"  runs_root:            {runs_root}")
        print(f"  runs_scanned:         {report.runs_scanned}")
        print(f"  runs_abandoned:       {report.runs_abandoned}")
        print(f"  runs_preserved_pinned:{report.runs_preserved_pinned}")
        print(f"  runs_preserved_prot:  {report.runs_preserved_protected}")
        print(f"  runs_in_grace_window: {report.runs_in_grace_window}")
        print(f"  runs_skipped_terminal:{report.runs_skipped_terminal}")
        print(f"  runs_skipped_malformed:{report.runs_skipped_malformed}")
        print(f"  duration_ms:          {report.duration_ms:.2f}")
        if report.abandoned_run_ids:
            print("  abandoned_run_ids:")
            for rid in report.abandoned_run_ids:
                print(f"    - {rid}")
        print(f"  candidate_refs_collected: {len(candidate_report['collected'])}")
        print(f"  candidate_refs_retained:  {len(candidate_report['retained'])}")
        if report.near_stale_run_ids:
            print("  near_stale_run_ids:")
            for rid in report.near_stale_run_ids:
                print(f"    - {rid}")

    sys.exit(0)
