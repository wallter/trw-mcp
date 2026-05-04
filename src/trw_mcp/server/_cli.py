"""CLI entry point and argument parser for trw-mcp.

Provides ``main()`` which is the ``trw-mcp`` console_script entry point,
the argument parser builder, and the ``_check_mcp_json_portability`` helper.
"""

from __future__ import annotations

import argparse
import difflib
from pathlib import Path

import structlog

from trw_mcp import __version__
from trw_mcp._logging import configure_logging
from trw_mcp.models.config import TRWConfig, get_config, reload_config
from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS


def _check_mcp_json_portability(cwd: Path | None = None) -> None:
    """Warn if ``.mcp.json`` contains a stale absolute path for the trw server.

    Reads ``.mcp.json`` from *cwd* (or ``Path.cwd()``) and checks whether the
    ``mcpServers.trw.command`` value is an absolute path that no longer exists
    on disk.  Logs a warning with remediation instructions if so.

    Does NOT log full file contents (security: may contain API keys for
    other servers).

    Args:
        cwd: Directory to look for ``.mcp.json``.  Defaults to current
            working directory.  Accepts an explicit path for testability.
    """
    import json as _json

    target = cwd or Path.cwd()
    mcp_path = target / ".mcp.json"
    if not mcp_path.exists():
        return

    try:
        data = _json.loads(mcp_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return  # malformed or unreadable -- not our problem here

    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        return
    trw_entry = servers.get("trw")
    if not isinstance(trw_entry, dict):
        return

    cmd = str(trw_entry.get("command", ""))
    if cmd.startswith("/") and not Path(cmd).exists():
        log = structlog.get_logger(__name__)
        log.warning(
            "stale_mcp_json_path",
            command=cmd,
            fix="run 'trw-mcp update-project .' to fix",
        )


# ── Argument parser ──────────────────────────────────────────────────────



# _build_arg_parser extracted to _cli_argparse (PRD-DIST-243 batch 20).
# Re-exported for back-compat with test_devex_fix065 + test_cli_auth_subcommand.
from trw_mcp.server._cli_argparse import _build_arg_parser as _build_arg_parser



def _suggest_command(unknown: str, parser: argparse.ArgumentParser) -> str | None:
    """Return the closest known subcommand to *unknown*, or None if no good match.

    Uses ``difflib.get_close_matches`` with cutoff=0.6 to find typo corrections.
    """
    known: list[str] = []
    for action in parser._subparsers._actions:  # type: ignore[union-attr]
        if isinstance(action, argparse._SubParsersAction):
            known.extend(action.choices.keys())
    matches = difflib.get_close_matches(unknown, known, n=1, cutoff=0.5)
    return matches[0] if matches else None


def _apply_cli_security_overrides(config: TRWConfig, args: argparse.Namespace) -> TRWConfig:
    allow_unsigned = getattr(args, "allow_unsigned", None)
    if allow_unsigned is None:
        return config
    return config.model_copy(
        update={
            "security": config.security.model_copy(
                update={"mcp": config.security.mcp.model_copy(update={"allow_unsigned": bool(allow_unsigned)})}
            )
        }
    )


def _should_run_boot_sequence(args: argparse.Namespace, config: TRWConfig) -> bool:
    """Return whether this process should run mutating boot maintenance.

    In shared HTTP mode, the foreground stdio process is only a thin proxy to
    an already-running or auto-started HTTP server. Running boot GC in every
    proxy process blocks MCP initialize/reconnect for tens of seconds in large
    repos and duplicates maintenance that belongs to the shared server.

    Direct server processes still run boot maintenance:
    - explicit ``--transport streamable-http|sse|stdio``;
    - default standalone stdio when the project config uses ``mcp_transport:
      stdio``.
    """

    requested_transport = getattr(args, "transport", None)
    if requested_transport is not None:
        return True
    return config.mcp_transport == "stdio"


def main() -> None:
    """Entry point for the trw-mcp CLI command.

    Parses arguments, dispatches to subcommand handlers, or starts the
    MCP server with the appropriate transport.
    """
    import logging as _logging
    import sys as _sys

    # Early stderr logging so exceptions during config load are visible.
    # This is replaced by configure_logging() once config is loaded.
    _logging.basicConfig(
        format="%(levelname)s: %(message)s",
        level=_logging.DEBUG,
        stream=_sys.stderr,
        force=True,
    )

    parser = _build_arg_parser()
    args = parser.parse_args()

    # Resolve shared CLI logging state before dispatching subcommands so they
    # don't inherit the noisy fallback stdlib logger.
    debug = bool(getattr(args, "debug", False))
    verbosity = int(getattr(args, "verbose", 0))
    if getattr(args, "quiet", False):
        verbosity = -1
    elif debug and verbosity == 0:
        verbosity = 1

    is_subcommand = bool(args.command and args.command != "serve")
    plain_subcommand_output = is_subcommand and not (debug or verbosity > 0 or getattr(args, "log_json", False))
    effective_log_level = getattr(args, "log_level", None)
    if plain_subcommand_output and effective_log_level is None:
        effective_log_level = "WARNING"

    subcommand_log_dir: Path | None = None
    if debug or verbosity >= 2:
        trw_dir = getattr(TRWConfig(), "trw_dir", ".trw")
        logs_dir = getattr(TRWConfig(), "logs_dir", "logs")
        subcommand_log_dir = Path.cwd() / trw_dir / logs_dir

    configure_logging(
        debug=debug,
        verbosity=verbosity,
        log_level=effective_log_level,
        json_output=args.log_json or None,
        log_dir=subcommand_log_dir,
        package_name="trw-mcp",
    )

    # Dispatch subcommands
    cmd = str(args.command or "")
    handler = SUBCOMMAND_HANDLERS.get(cmd)
    if handler is not None:
        handler(args)
        return

    # If unrecognized subcommand (not empty, not "serve"), suggest closest match
    if cmd and cmd != "serve":
        suggestion = _suggest_command(cmd, parser)
        if suggestion:
            print(f"Unknown command '{cmd}'. Did you mean '{suggestion}'?")
        else:
            print(f"Unknown command '{cmd}'. Run 'trw-mcp --help' for available commands.")
        _sys.exit(1)

    # Default: run MCP server (no subcommand or "serve")
    config = _apply_cli_security_overrides(get_config(), args)
    reload_config(config)
    debug = args.debug or config.debug

    # Resolve verbosity: --quiet overrides, --debug adds to -v count
    verbosity = args.verbose
    if args.quiet:
        verbosity = -1
    elif debug and verbosity == 0:
        verbosity = 1

    log_dir: Path | None = None
    if debug or verbosity >= 2:
        log_dir = Path.cwd() / config.trw_dir / config.logs_dir

    configure_logging(
        debug=debug,
        verbosity=verbosity,
        log_level=args.log_level,
        json_output=args.log_json or None,
        log_dir=log_dir,
        package_name="trw-mcp",
    )

    # PRD-FIX-037: Warn if .mcp.json has a stale absolute path
    _check_mcp_json_portability()

    log = structlog.get_logger(__name__)

    # PRD-CORE-141 FR09: direct server boot-time stale-run sweep runs BEFORE
    # FastMCP starts accepting connections. In shared HTTP mode, foreground
    # stdio processes are proxies and must not block reconnect on this sweep.
    # Wrapped in try/except (NFR02 fail-open) — sweep failure MUST NOT block
    # direct server startup.
    if _should_run_boot_sequence(args, config):
        _boot_sequence(config, log)
    else:
        log.info(
            "boot_gc_skipped_proxy_mode",
            reason="stdio_proxy_to_shared_http",
            target_transport=config.mcp_transport,
            target_host=config.mcp_host,
            target_port=config.mcp_port,
        )

    from trw_mcp.server._transport import resolve_and_run_transport

    resolve_and_run_transport(args, config, debug=debug, log=log)


def _boot_sequence(
    config: TRWConfig,
    log: structlog.stdlib.BoundLogger,
) -> None:
    """Run boot-time maintenance (pin-store recovery + stale-run sweep).

    PRD-CORE-141 FR09: executed from :func:`main` after config+logging are
    resolved and BEFORE :func:`trw_mcp.server._transport.resolve_and_run_transport`
    spawns the FastMCP server.  This is the single authoritative boot site.

    Fail-open (NFR02): any exception is logged with a full traceback and
    control returns to the caller so the server starts anyway.  A stale-sweep
    bug must never take the server down.

    Args:
        config: Fully-resolved :class:`TRWConfig` — determines whether the
            sweep runs (``cleanup_on_boot``) and which TTL/grace thresholds
            to use.
        log: Structured logger.
    """
    if not config.cleanup_on_boot:
        log.info("boot_gc_skipped_config", reason="cleanup_on_boot=False")
        return

    try:
        import time as _time
        from datetime import datetime as _datetime
        from datetime import timezone as _timezone
        from pathlib import Path as _Path

        from trw_mcp.state._paths import resolve_project_root
        from trw_mcp.state._pin_store import load_pin_store, prune_pin_store_orphans
        from trw_mcp.state._run_gc import sweep_stale_runs

        project_root = resolve_project_root()
        runs_root = project_root / config.runs_root

        # Persist eviction of orphan-pid / stale-path pins so they stop
        # firing pin_orphan_evicted warnings on every load.
        try:
            prune_pin_store_orphans()
        except Exception:  # justified: NFR02 — prune failure must never block server start
            log.warning("boot_pin_prune_failed", exc_info=True)

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
                    hb_ts = heartbeat_raw.rstrip("Z")
                    hb_dt = _datetime.fromisoformat(hb_ts)
                    hb_unix = hb_dt.replace(tzinfo=_timezone.utc).timestamp()
                    if now - hb_unix > pin_ttl_seconds:
                        continue
                except ValueError:
                    pass  # malformed heartbeat — be conservative, keep pin
            pinned_paths.append(_Path(run_path_raw))

        sweep_stale_runs(
            runs_root,
            config.run_staleness_hours,
            config.run_staleness_grace_hours,
            pinned_paths,
            dry_run=False,
        )
    except Exception:  # justified: NFR02 — sweep failure must never block server start
        log.warning("boot_gc_failed", exc_info=True)
