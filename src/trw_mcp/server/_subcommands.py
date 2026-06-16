"""CLI subcommand handlers for trw-mcp.

Each function handles one CLI subcommand (init-project, update-project,
audit, export, import-learnings, build-release).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

import structlog

from trw_mcp.server._subcommands_check import (
    _check_instructions_core as _check_instructions_core,
)
from trw_mcp.server._subcommands_check import (
    _run_check_instructions as _run_check_instructions,
)
from trw_mcp.server._subcommands_doctor import (
    _doctor_core as _doctor_core,
)
from trw_mcp.server._subcommands_doctor import (
    _run_doctor as _run_doctor,
)
from trw_mcp.server._subcommands_lifecycle import (
    _run_auth as _run_auth,
)
from trw_mcp.server._subcommands_lifecycle import (
    _run_uninstall as _run_uninstall,
)
from trw_mcp.server._subcommands_misc import (
    _run_config_reference as _run_config_reference,
)
from trw_mcp.server._subcommands_misc import (
    _run_local as _run_local,
)
from trw_mcp.server._subcommands_release import (
    _get_framework_version as _get_framework_version,
)
from trw_mcp.server._subcommands_release import (
    _push_release as _push_release,
)
from trw_mcp.server._subcommands_release import (
    _run_build_release as _run_build_release,
)
from trw_mcp.server._subcommands_release import (
    _run_version_status as _run_version_status,
)

logger = structlog.get_logger(__name__)


def _is_detailed_cli(args: argparse.Namespace) -> bool:
    """Return whether CLI output should use structured per-event progress."""
    return bool(getattr(args, "log_json", False) or getattr(args, "debug", False) or getattr(args, "verbose", 0) > 0)


def _is_quiet_cli(args: argparse.Namespace) -> bool:
    """Return whether human-friendly output should be suppressed."""
    return bool(getattr(args, "quiet", False))


def _print_cli_line(message: str, *, stream: TextIO | None = None) -> None:
    """Print a single CLI line without leaking formatting logic everywhere."""
    print(message, file=stream or sys.stdout)


def _summarize_update_result(result: dict[str, list[str]], *, target: Path, dry_run: bool, ide: str | None) -> None:
    """Render a concise human summary for update-project."""
    updated = len(result["updated"])
    created = len(result["created"])
    preserved = len(result["preserved"])
    cleaned = len(result.get("cleaned", []))
    errors = len(result["errors"])
    warnings = result.get("warnings", [])

    codex_touched = any(
        path.startswith((".codex/", ".agents/skills/")) or path == "AGENTS.md"
        for path in [*result["updated"], *result["created"]]
    )

    mode_label = "dry run" if dry_run else "complete"
    _print_cli_line(f"TRW update {mode_label}")
    _print_cli_line(f"Project: {target}")
    _print_cli_line(
        f"Changes: {updated} updated, {created} created, {preserved} preserved, {cleaned} cleaned, {errors} errors"
    )
    if ide:
        _print_cli_line(f"Target IDE: {ide}")
    if codex_touched:
        _print_cli_line("Codex: managed config uses [features].hooks; hooks, agents, skills, and AGENTS.md synced")
    if warnings:
        _print_cli_line("")
        _print_cli_line("Warnings:")
        for warning in warnings:
            _print_cli_line(f"- {warning}")
    if not dry_run:
        _print_cli_line("")
        _print_cli_line("Use -v for per-file changes or --log-json for structured output.")


def _run_init_project(args: argparse.Namespace) -> None:
    """Handle the ``init-project`` subcommand."""
    from trw_mcp.bootstrap import init_project

    target = Path(args.target_dir).resolve()
    detailed = _is_detailed_cli(args)
    quiet = _is_quiet_cli(args)

    def _progress(action: str, path: str) -> None:
        if detailed:
            logger.info("init_progress", op="init_project", action=action, path=path)
        elif not quiet and action == "Phase":
            _print_cli_line(f"==> {path}")

    result = init_project(
        target,
        force=args.force,
        source_package=args.source_package,
        test_path=args.test_path,
        runs_root=getattr(args, "runs_root", ".trw/runs"),
        ide=getattr(args, "ide", None),
        on_progress=_progress,
    )

    for e in result["errors"]:
        logger.error("init_project_error", op="init_project", error=str(e))
    if detailed:
        for w in result.get("warnings", []):
            logger.warning("init_project_warning", op="init_project", detail=str(w))

    if not result["errors"]:
        if detailed:
            logger.info("init_project_complete", op="init_project", target=str(target))
        elif not quiet:
            _print_cli_line("TRW initialization complete")
            _print_cli_line(f"Project: {target}")
            preserved = result.get("preserved", [])
            updated = result.get("updated", [])
            _print_cli_line(
                f"Changes: {len(updated)} updated, {len(result['created'])} created, {len(preserved)} preserved"
            )
            warnings = result.get("warnings", [])
            if warnings:
                _print_cli_line("")
                _print_cli_line("Warnings:")
                for warning in warnings:
                    _print_cli_line(f"- {warning}")
            _print_cli_line("")
            _print_cli_line("Next: run your AI coding tool in this directory.")

    sys.exit(1 if result["errors"] else 0)


def _run_update_project(args: argparse.Namespace) -> None:
    """Handle the ``update-project`` subcommand."""
    from trw_mcp.bootstrap import update_project

    target = Path(args.target_dir).resolve()
    dry_run: bool = getattr(args, "dry_run", False)
    detailed = _is_detailed_cli(args)
    quiet = _is_quiet_cli(args)

    def _progress(action: str, path: str) -> None:
        if detailed:
            logger.info("update_progress", op="update_project", action=action, path=path)
        elif not quiet and action == "Phase":
            _print_cli_line(f"==> {path}")

    result = update_project(
        target,
        pip_install=args.pip_install,
        dry_run=dry_run,
        ide=getattr(args, "ide", None),
        on_progress=_progress,
    )

    if detailed:
        for w in result.get("warnings", []):
            logger.warning("update_project_warning", op="update_project", detail=str(w))
    elif not quiet and result.get("warnings"):
        pass
    for e in result["errors"]:
        logger.error("update_project_error", op="update_project", error=str(e))

    total = len(result["updated"]) + len(result["created"])
    if not result["errors"]:
        if detailed:
            logger.info(
                "update_project_complete",
                op="update_project",
                target=str(target),
                total_files=total,
                dry_run=dry_run,
            )
        elif not quiet:
            _summarize_update_result(result, target=target, dry_run=dry_run, ide=getattr(args, "ide", None))

    sys.exit(1 if result["errors"] else 0)


def _run_audit(args: argparse.Namespace) -> None:
    """Handle the ``audit`` subcommand."""
    from trw_mcp.audit import format_markdown, run_audit

    target = Path(args.target_dir).resolve()
    result = run_audit(target, fix=args.fix)

    if result.get("status") == "failed":
        logger.error("audit_failed", op="audit", error=str(result.get("error", "unknown")))
        sys.exit(1)

    if args.format == "json":
        output = json.dumps(result, indent=2, default=str)
    else:
        output = format_markdown(result)

    if args.output:
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        logger.info("audit_report_written", op="audit", path=str(out_path))
    else:
        logger.info("audit_report_output", op="audit", output=output)

    sys.exit(0)


def _run_export(args: argparse.Namespace) -> None:
    """Handle the ``export`` subcommand."""
    from trw_mcp.export import export_data

    target = Path(args.target_dir).resolve()
    result = export_data(
        target,
        args.scope,
        fmt=args.format,
        since=getattr(args, "since", None),
        min_impact=getattr(args, "min_impact", 0.0),
    )

    if result.get("status") == "failed":
        logger.error("export_failed", op="export", error=str(result.get("error", "unknown")))
        sys.exit(1)

    # CSV output for learnings
    if args.format == "csv" and "learnings_csv" in result:
        output = str(result["learnings_csv"])
    else:
        output = json.dumps(result, indent=2, default=str)

    if args.output:
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        logger.info("export_written", op="export", path=str(out_path))
    else:
        logger.info("export_output", op="export", output=output)

    sys.exit(0)


def _run_import_learnings(args: argparse.Namespace) -> None:
    """Handle the ``import-learnings`` subcommand."""
    from trw_mcp.export import import_learnings

    source = Path(args.source_file).resolve()
    target = Path(args.target_dir).resolve()

    tag_list: list[str] | None = None
    if args.tags:
        tag_list = [t.strip() for t in args.tags.split(",")]

    result = import_learnings(
        source,
        target,
        min_impact=args.min_impact,
        tags=tag_list,
        dry_run=args.dry_run,
    )

    if result.get("status") == "failed":
        logger.error(
            "import_learnings_failed",
            op="import_learnings",
            error=str(result.get("error", "unknown")),
        )
        sys.exit(1)

    logger.info(
        "import_learnings_complete",
        op="import_learnings",
        dry_run=args.dry_run,
        source_project=str(result.get("source_project", "unknown")),
        total_source=result.get("total_source", 0),
        imported=result.get("imported", 0),
        skipped_duplicate=result.get("skipped_duplicate", 0),
        skipped_filter=result.get("skipped_filter", 0),
    )

    sys.exit(0)


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

    # Assemble live-pinned paths — pins whose heartbeat is within pin_ttl_hours
    # and whose owning pid is alive.  load_pin_store already applies the
    # orphan-pid + stale-path eviction, so we only need the heartbeat TTL
    # filter here.
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

    if as_json:
        print(json.dumps(asdict(report), indent=2))
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
        if report.near_stale_run_ids:
            print("  near_stale_run_ids:")
            for rid in report.near_stale_run_ids:
                print(f"    - {rid}")

    sys.exit(0)


def _run_channel_doctor(args: argparse.Namespace) -> None:
    """Lazy dispatch to channel-doctor implementation (PRD-DIST-2400 FR18)."""
    from trw_mcp.cli.channel_doctor import run_channel_doctor

    run_channel_doctor(args)


def _run_tendencies(args: argparse.Namespace) -> None:
    """Lazy dispatch to the ``tendencies`` report CLI (PRD-QUAL-109 FR-03).

    Advisory only: runs every registered tendency detector over the corpus,
    prints the findings, and exits 0 regardless of findings (never blocking).
    Kept thin (and the implementation in the ``tendencies`` package) so this
    facade stays under the eLOC gate.
    """
    from trw_mcp.tendencies.cli import run_tendencies

    run_tendencies(args)


def _run_session_changelog(args: argparse.Namespace) -> None:
    """Handle the ``session-changelog`` subcommand (PRD-LOCAL-049 FR04).

    Regenerate/print the session changelog for a given run path. Read-only by
    default (prints markdown to stdout); ``--write`` persists the artifact to
    ``<run>/reports/session-changelog.md`` and prints its path. No generic
    ``trw changelog`` command is introduced — this is package-local to trw-mcp.
    """
    from trw_mcp.state._paths import resolve_trw_dir
    from trw_mcp.state._session_changelog import build_session_changelog, write_session_changelog

    run_path = Path(args.run_path).resolve()
    if not (run_path / "meta").is_dir():
        logger.error("session_changelog_run_not_found", op="session-changelog", run_path=str(run_path))
        print(f"Not a TRW run directory (no meta/): {run_path}", file=sys.stderr)
        sys.exit(1)

    trw_dir = resolve_trw_dir()
    advisory = bool(getattr(args, "advisory", False))

    if getattr(args, "write", False):
        report_path, _ = write_session_changelog(run_path, trw_dir, changelog_advisory_enabled=advisory)
        print(str(report_path))
    else:
        result = build_session_changelog(run_path, trw_dir, changelog_advisory_enabled=advisory)
        print(result.markdown)

    sys.exit(0)


SUBCOMMAND_HANDLERS: dict[str, Callable[[argparse.Namespace], None]] = {
    "init-project": _run_init_project,
    "update-project": _run_update_project,
    "audit": _run_audit,
    "export": _run_export,
    "import-learnings": _run_import_learnings,
    "build-release": _run_build_release,
    "version-status": _run_version_status,
    "auth": _run_auth,
    "uninstall": _run_uninstall,
    "config-reference": _run_config_reference,
    "local": _run_local,
    "check-instructions": _run_check_instructions,
    "doctor": _run_doctor,
    "gc": _run_gc,
    "channel-doctor": _run_channel_doctor,
    "session-changelog": _run_session_changelog,
    "tendencies": _run_tendencies,
}


# PRD-DIST-1996 (c748): tier entitlement provisioning subcommand.
# Lazy-imported to avoid circular import with _entitlements.
def _run_tier_lazy(args: argparse.Namespace) -> None:
    from trw_mcp.server._subcommands_tier import run_tier

    run_tier(args)


SUBCOMMAND_HANDLERS["tier"] = _run_tier_lazy
