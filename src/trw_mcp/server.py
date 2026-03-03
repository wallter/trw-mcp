"""TRW MCP Server -- orchestration, requirements, and self-learning tools.

FastMCP server entry point. Registers all tools, resources, and prompts.
Run with: ``trw-mcp`` CLI or ``trw-mcp --debug`` for file logging.

PRD-CORE-001: Base MCP tool suite.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.middleware.ceremony import CeremonyMiddleware
from trw_mcp.models.config import TRWConfig  # used in main()


def _configure_logging(*, debug: bool, config: TRWConfig) -> None:
    """Configure structlog processors and stdlib logging.

    Args:
        debug: When True, enables file logging to .trw/logs/ and
            dev-friendly console output on stderr at DEBUG level.
        config: TRW configuration for path resolution.
    """
    log_level = logging.DEBUG if debug else logging.INFO

    base_processors: list[structlog.types.Processor] = [
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.contextvars.merge_contextvars,
        structlog.processors.StackInfoRenderer(),
    ]

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]

    if debug:
        logs_dir = Path.cwd() / config.trw_dir / config.logs_dir
        logs_dir.mkdir(parents=True, exist_ok=True)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = logs_dir / f"trw-mcp-{today}.jsonl"

        handlers.append(logging.FileHandler(str(log_file), encoding="utf-8"))
        base_processors.append(structlog.processors.format_exc_info)

        # Suppress FastMCP / Redis / HTTP noise (~1.25M lines/day, 145 MB vs ~800 TRW events)
        for logger_name in (
            "fastmcp", "redis", "redis.asyncio", "redis.connection",
            "httpcore", "httpx", "asyncio", "urllib3",
        ):
            logging.getLogger(logger_name).setLevel(logging.WARNING)

        # Filter non-JSON lines from file handler (catches raw Redis >>> protocol output)
        class _JsonOnlyFilter(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                msg = str(record.getMessage())
                return msg.startswith("{") or msg.startswith("[")

        for handler in handlers:
            if isinstance(handler, logging.FileHandler):
                handler.addFilter(_JsonOnlyFilter())

    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        handlers=handlers,
        force=True,
    )

    structlog.configure(
        processors=[
            *base_processors,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
    )


_DEFAULT_INSTRUCTIONS = (
    "TRW gives you engineering memory that persists across sessions "
    "\u2014 patterns, gotchas, and project knowledge that accumulate over time. "
    "Call trw_session_start() first to load your prior learnings and any active run state. "
    "Workflow: trw_session_start \u2192 work \u2192 trw_learn (discoveries) \u2192 trw_deliver. "
    "Without trw_deliver, your learnings from this session are lost to future agents."
)


def _load_server_instructions() -> str:
    """Load MCP server instructions from centralized messages, with fallback."""
    from trw_mcp.prompts.messaging import get_message_or_default

    return get_message_or_default("server_instructions", _DEFAULT_INSTRUCTIONS)


mcp = FastMCP(
    "trw",
    instructions=_load_server_instructions(),
    middleware=[CeremonyMiddleware()],
)


def _register_tools() -> None:
    """Register all tools, resources, and prompts on the MCP server."""
    from trw_mcp.prompts.aaref import register_aaref_prompts
    from trw_mcp.resources.config import register_config_resources
    from trw_mcp.resources.run_state import register_run_state_resources
    from trw_mcp.resources.templates import register_template_resources
    from trw_mcp.tools.build import register_build_tools
    from trw_mcp.tools.ceremony import register_ceremony_tools
    from trw_mcp.tools.checkpoint import register_checkpoint_tools
    from trw_mcp.tools.knowledge import register_knowledge_tools
    from trw_mcp.tools.learning import register_learning_tools
    from trw_mcp.tools.orchestration import register_orchestration_tools
    from trw_mcp.tools.report import register_report_tools
    from trw_mcp.tools.requirements import register_requirements_tools
    from trw_mcp.tools.review import register_review_tools
    from trw_mcp.tools.usage import register_usage_tools

    register_build_tools(mcp)
    register_ceremony_tools(mcp)
    register_checkpoint_tools(mcp)
    register_knowledge_tools(mcp)
    register_learning_tools(mcp)
    register_orchestration_tools(mcp)
    register_report_tools(mcp)
    register_requirements_tools(mcp)
    register_review_tools(mcp)
    register_usage_tools(mcp)

    register_config_resources(mcp)
    register_run_state_resources(mcp)
    register_template_resources(mcp)

    register_aaref_prompts(mcp)


# Eager registration so tools are available via `fastmcp run` and test imports.
_register_tools()


def _run_init_project(args: argparse.Namespace) -> None:
    """Handle the ``init-project`` subcommand."""
    from trw_mcp.bootstrap import init_project

    target = Path(args.target_dir).resolve()
    result = init_project(
        target,
        force=args.force,
        source_package=args.source_package,
        test_path=args.test_path,
    )

    for f in result["created"]:
        print(f"  Created: {f}")
    for f in result["skipped"]:
        print(f"  Skipped (exists): {f}")
    for e in result["errors"]:
        print(f"  ERROR: {e}")

    if not result["errors"]:
        print(f"\nTRW framework initialized in {target}")
        print("Next steps:")
        print("  1. Edit CLAUDE.md with your project details")
        print("  2. Run `trw-mcp` to start the MCP server")
        print("  3. In Claude Code, run /mcp to connect")
        print("  4. Call trw_session_start() to begin")

    sys.exit(1 if result["errors"] else 0)


def _run_update_project(args: argparse.Namespace) -> None:
    """Handle the ``update-project`` subcommand."""
    from trw_mcp.bootstrap import update_project

    target = Path(args.target_dir).resolve()
    dry_run: bool = getattr(args, "dry_run", False)
    result = update_project(target, pip_install=args.pip_install, dry_run=dry_run)

    prefix = "[DRY RUN] " if dry_run else ""
    for f in result["updated"]:
        print(f"  {prefix}Updated: {f}")
    for f in result["created"]:
        print(f"  {prefix}Created (new): {f}")
    for f in result["preserved"]:
        print(f"  Preserved: {f}")
    for w in result.get("warnings", []):
        print(f"  WARNING: {w}")
    for e in result["errors"]:
        print(f"  ERROR: {e}")

    total = len(result["updated"]) + len(result["created"])
    if not result["errors"]:
        verb = "would update" if dry_run else "updated"
        print(f"\nTRW framework {verb} in {target} ({total} files)")

    sys.exit(1 if result["errors"] else 0)


def _run_audit(args: argparse.Namespace) -> None:
    """Handle the ``audit`` subcommand."""
    from trw_mcp.audit import format_markdown, run_audit

    target = Path(args.target_dir).resolve()
    result = run_audit(target, fix=args.fix)

    if result.get("status") == "failed":
        print(f"  ERROR: {result.get('error', 'unknown')}")
        sys.exit(1)

    if args.format == "json":
        import json

        output = json.dumps(result, indent=2, default=str)
    else:
        output = format_markdown(result)

    if args.output:
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"  Audit report written to: {out_path}")
    else:
        print(output)

    sys.exit(0)


def _run_export(args: argparse.Namespace) -> None:
    """Handle the ``export`` subcommand."""
    import json

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
        print(f"  ERROR: {result.get('error', 'unknown')}")
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
        print(f"  Export written to: {out_path}")
    else:
        print(output)

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
        print(f"  ERROR: {result.get('error', 'unknown')}")
        sys.exit(1)

    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"  {prefix}Source: {result.get('source_project', 'unknown')} ({result.get('total_source', 0)} entries)")
    print(f"  {prefix}Imported: {result.get('imported', 0)}")
    print(f"  {prefix}Skipped (duplicate): {result.get('skipped_duplicate', 0)}")
    print(f"  {prefix}Skipped (filter): {result.get('skipped_filter', 0)}")

    sys.exit(0)


def _run_build_release(args: argparse.Namespace) -> None:
    """Handle the ``build-release`` subcommand."""
    from trw_mcp.release_builder import build_release_bundle

    version: str | None = getattr(args, "version", None)
    output_dir = Path(getattr(args, "output_dir", ".")).resolve()

    result = build_release_bundle(version=version, output_dir=output_dir)

    print(f"  Bundle: {result['path']}")
    print(f"  Version: {result['version']}")
    print(f"  SHA-256: {result['checksum']}")
    print(f"  Size: {result['size_bytes']} bytes")

    push = getattr(args, "push", False)
    if push:
        backend_url = getattr(args, "backend_url", None)
        api_key = getattr(args, "api_key", None)
        if not backend_url or not api_key:
            print("  ERROR: --push requires --backend-url and --api-key")
            sys.exit(1)
        _push_release(result, backend_url, api_key)

    sys.exit(0)


def _push_release(result: dict[str, object], backend_url: str, api_key: str) -> None:
    """Push release metadata to the backend."""
    import json
    import urllib.request

    url = f"{backend_url.rstrip('/')}/v1/releases"
    payload = json.dumps({
        "version": str(result["version"]),
        "artifact_url": str(result["path"]),
        "artifact_checksum": str(result["checksum"]),
        "artifact_size_bytes": int(str(result["size_bytes"])),
        "framework_version": _get_framework_version(),
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"  Published: v{data.get('version', '?')} to {backend_url}")
    except Exception as exc:
        print(f"  ERROR publishing: {exc}")
        sys.exit(1)


def _get_framework_version() -> str:
    """Extract framework version from bundled FRAMEWORK.md."""
    fw_path = Path(__file__).parent / "data" / "framework.md"
    if fw_path.exists():
        first_line = fw_path.read_text(encoding="utf-8").split("\n", 1)[0]
        # "v24.1_TRW — CLAUDE CODE..."
        return first_line.split("—")[0].strip().split()[0] if "—" in first_line else first_line.split()[0]
    return "unknown"


def _is_port_open(host: str, port: int) -> bool:
    """Check if a TCP port is accepting connections."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex((host, port)) == 0


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    try:
        os.kill(pid, 0)  # Signal 0: check existence without sending a signal
        return True
    except (OSError, ProcessLookupError):
        return False


def _ensure_http_server(
    config: TRWConfig,
    log: structlog.stdlib.BoundLogger,
    *,
    debug: bool = False,
) -> str | None:
    """Ensure the shared HTTP MCP server is running.

    Auto-starts the server daemon if not already running, using file lock
    to prevent race conditions between concurrent Claude Code instances.
    Cleans up stale PID files from dead processes before attempting start.

    Returns the server URL on success, None on failure (caller should
    fall back to standalone stdio).
    """
    import fcntl
    import subprocess
    import time

    host = config.mcp_host
    port = config.mcp_port
    transport = config.mcp_transport
    path = "/sse" if transport == "sse" else "/mcp"
    url = f"http://{host}:{port}{path}"

    # Already running — fast path
    if _is_port_open(host, port):
        log.info("mcp_server_already_running", host=host, port=port)
        return url

    trw_dir = Path.cwd() / config.trw_dir
    trw_dir.mkdir(parents=True, exist_ok=True)
    lock_path = trw_dir / "mcp-server.lock"
    pid_path = trw_dir / "mcp-server.pid"

    # Clean up stale PID file from dead processes
    if pid_path.exists():
        try:
            old_pid = int(pid_path.read_text(encoding="utf-8").strip())
            if not _is_pid_alive(old_pid):
                log.info("mcp_server_stale_pid_cleaned", stale_pid=old_pid)
                pid_path.unlink(missing_ok=True)
        except (ValueError, OSError):
            pid_path.unlink(missing_ok=True)

    lock_fd = open(lock_path, "w")  # noqa: SIM115
    try:
        # Non-blocking lock attempt
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Another process holds the lock — wait, then re-check port
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        if _is_port_open(host, port):
            return url

    try:
        cmd = [
            sys.executable, "-m", "trw_mcp.server",
            "--transport", transport,
            "--host", host,
            "--port", str(port),
        ]
        if debug:
            cmd.append("--debug")

        logs_dir = trw_dir / config.logs_dir
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / "mcp-server.log"

        with open(log_file, "a") as log_out:  # noqa: SIM115
            proc = subprocess.Popen(
                cmd,
                stdout=log_out,
                stderr=log_out,
                start_new_session=True,
            )

        pid_path.write_text(str(proc.pid), encoding="utf-8")

        # Poll for port availability (0.5s intervals, 30s max — WSL2 cold starts
        # can take 15-20s due to filesystem I/O latency)
        for _ in range(60):
            time.sleep(0.5)
            if _is_port_open(host, port):
                log.info("mcp_server_started", pid=proc.pid, url=url)
                return url

        log.warning("mcp_server_start_timeout", host=host, port=port, timeout_secs=30)
        return None
    except Exception:
        log.warning("mcp_server_start_failed", exc_info=True)
        return None
    finally:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()


async def _run_stdio_proxy(url: str, max_retries: int = 3) -> None:
    """Bridge stdio transport to a shared HTTP MCP server.

    Creates a lightweight proxy that forwards all MCP operations (tools,
    resources, prompts) from Claude Code (via stdio) to the shared HTTP
    server. Uses MCP SDK primitives — no external dependencies.

    Retries initial connection up to ``max_retries`` times with exponential
    backoff to handle race conditions where the server is still starting.
    """
    import asyncio as _asyncio

    from mcp import types
    from pydantic import AnyUrl
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    log = structlog.get_logger()

    # Retry loop for initial connection (server may still be starting)
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            async with streamable_http_client(url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    # Discover remote capabilities once at startup
                    tools_result = await session.list_tools()
                    resources_result = await session.list_resources()
                    prompts_result = await session.list_prompts()

                    log.info(
                        "stdio_proxy_connected",
                        url=url,
                        tools=len(tools_result.tools),
                        attempt=attempt + 1,
                    )

                    proxy = Server("trw-proxy")

                    @proxy.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
                    async def handle_list_tools() -> list[types.Tool]:
                        return tools_result.tools

                    @proxy.call_tool(validate_input=False)  # type: ignore[untyped-decorator]
                    async def handle_call_tool(
                        name: str, arguments: dict[str, object] | None = None,
                    ) -> types.CallToolResult:
                        return await session.call_tool(name, arguments)

                    @proxy.list_resources()  # type: ignore[no-untyped-call, untyped-decorator]
                    async def handle_list_resources() -> list[types.Resource]:
                        return resources_result.resources

                    @proxy.read_resource()  # type: ignore[no-untyped-call, untyped-decorator]
                    async def handle_read_resource(uri: AnyUrl) -> str:
                        result = await session.read_resource(uri)
                        if result.contents:
                            c = result.contents[0]
                            text = getattr(c, "text", None)
                            if text is not None:
                                return str(text)
                            blob = getattr(c, "blob", None)
                            if blob is not None:
                                return str(blob)
                        return ""

                    @proxy.list_prompts()  # type: ignore[no-untyped-call, untyped-decorator]
                    async def handle_list_prompts() -> list[types.Prompt]:
                        return prompts_result.prompts

                    @proxy.get_prompt()  # type: ignore[no-untyped-call, untyped-decorator]
                    async def handle_get_prompt(
                        name: str, arguments: dict[str, str] | None = None,
                    ) -> types.GetPromptResult:
                        return await session.get_prompt(name, arguments)

                    # Run the proxy on stdio — Claude Code communicates here
                    async with stdio_server() as (stdio_read, stdio_write):
                        await proxy.run(
                            stdio_read, stdio_write,
                            proxy.create_initialization_options(),
                        )
                    return  # Clean exit
        except (ConnectionError, OSError, Exception) as exc:
            last_error = exc
            if attempt < max_retries - 1:
                delay = 2 ** attempt  # 1s, 2s, 4s
                log.warning(
                    "stdio_proxy_connect_retry",
                    url=url,
                    attempt=attempt + 1,
                    delay_secs=delay,
                    error=str(exc),
                )
                await _asyncio.sleep(delay)

    # All retries exhausted
    log.error(
        "stdio_proxy_connect_failed",
        url=url,
        attempts=max_retries,
        last_error=str(last_error),
    )
    raise ConnectionError(
        f"Failed to connect to MCP server at {url} after {max_retries} attempts"
    ) from last_error


def main() -> None:
    """Entry point for the trw-mcp CLI command."""
    parser = argparse.ArgumentParser(
        prog="trw-mcp",
        description="TRW Framework MCP Server",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging to .trw/logs/ and stderr",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=None,
        help="MCP transport (default: from config or stdio)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Bind address for HTTP transport (default: from config or 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port for HTTP transport (default: from config or 8100)",
    )

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("serve", help="Run MCP server (default)")

    # init-project
    init_parser = subparsers.add_parser(
        "init-project", help="Bootstrap TRW in a project directory"
    )
    init_parser.add_argument(
        "target_dir",
        nargs="?",
        default=".",
        help="Target project directory (default: current directory)",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files",
    )
    init_parser.add_argument(
        "--source-package",
        default="",
        help="Source package name for build checks (e.g., myapp)",
    )
    init_parser.add_argument(
        "--test-path",
        default="",
        help="Test directory path relative to source (e.g., tests)",
    )

    # update-project
    update_parser = subparsers.add_parser(
        "update-project",
        help="Update TRW framework files (preserves user config)",
    )
    update_parser.add_argument(
        "target_dir",
        nargs="?",
        default=".",
        help="Target project directory (default: current directory)",
    )
    update_parser.add_argument(
        "--pip-install",
        action="store_true",
        help="Also reinstall the trw-mcp Python package",
    )
    update_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would change without modifying files",
    )

    # audit
    audit_parser = subparsers.add_parser(
        "audit",
        help="Run comprehensive TRW health audit on a project",
    )
    audit_parser.add_argument(
        "target_dir",
        nargs="?",
        default=".",
        help="Target project directory (default: current directory)",
    )
    audit_parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    audit_parser.add_argument(
        "--output",
        help="Write output to file instead of stdout",
    )
    audit_parser.add_argument(
        "--fix",
        action="store_true",
        help="Auto-prune duplicates and resync index",
    )

    # export
    export_parser = subparsers.add_parser(
        "export",
        help="Export TRW data (learnings, runs, analytics)",
    )
    export_parser.add_argument(
        "target_dir",
        nargs="?",
        default=".",
        help="Target project directory (default: current directory)",
    )
    export_parser.add_argument(
        "--scope",
        choices=["learnings", "runs", "analytics", "all"],
        default="all",
        help="Export scope (default: all)",
    )
    export_parser.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help="Output format (default: json, csv only for learnings)",
    )
    export_parser.add_argument(
        "--output",
        help="Write output to file instead of stdout",
    )
    export_parser.add_argument(
        "--since",
        help="ISO date filter (YYYY-MM-DD)",
    )
    export_parser.add_argument(
        "--min-impact",
        type=float,
        default=0.0,
        help="Minimum impact threshold for learnings",
    )

    # import-learnings
    import_parser = subparsers.add_parser(
        "import-learnings",
        help="Import learnings from an export file",
    )
    import_parser.add_argument(
        "source_file",
        help="Path to exported JSON file",
    )
    import_parser.add_argument(
        "target_dir",
        nargs="?",
        default=".",
        help="Target project directory (default: current directory)",
    )
    import_parser.add_argument(
        "--min-impact",
        type=float,
        default=0.0,
        help="Minimum impact threshold for import",
    )
    import_parser.add_argument(
        "--tags",
        help="Comma-separated tag filter",
    )
    import_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be imported without writing",
    )

    # build-release
    build_parser = subparsers.add_parser(
        "build-release",
        help="Build a release bundle (.tar.gz) of bundled data",
    )
    build_parser.add_argument(
        "--version",
        help="Release version (default: read from pyproject.toml)",
    )
    build_parser.add_argument(
        "--output-dir",
        default=".",
        help="Output directory for the bundle (default: current directory)",
    )
    build_parser.add_argument(
        "--push",
        action="store_true",
        help="Push release to backend after building",
    )
    build_parser.add_argument(
        "--backend-url",
        help="Backend API base URL (required with --push)",
    )
    build_parser.add_argument(
        "--api-key",
        help="API key for backend authentication (required with --push)",
    )

    args = parser.parse_args()

    if args.command == "init-project":
        _run_init_project(args)
        return

    if args.command == "update-project":
        _run_update_project(args)
        return

    if args.command == "audit":
        _run_audit(args)
        return

    if args.command == "export":
        _run_export(args)
        return

    if args.command == "import-learnings":
        _run_import_learnings(args)
        return

    if args.command == "build-release":
        _run_build_release(args)
        return

    # Default: run MCP server (no subcommand or "serve")
    config = TRWConfig()
    debug = args.debug or config.debug

    _configure_logging(debug=debug, config=config)

    log = structlog.get_logger()

    # ── Transport resolution (PRD-CORE-070-FR03) ────────────────────────
    # Path 1: Explicit --transport → run as that transport directly (server mode)
    # Path 2: No flag + config stdio → run stdio normally (default)
    # Path 3: No flag + config HTTP → auto-start shared server + stdio proxy
    if args.transport is not None:
        # Path 1: Direct server mode (e.g., spawned by _ensure_http_server)
        transport: str = args.transport
        host: str = args.host or config.mcp_host
        port: int = args.port or config.mcp_port

        log.info(
            "trw_server_initialized",
            tools_registered=True,
            debug_mode=debug,
            transport=transport,
            host=host,
            port=port,
            mode="direct",
        )

        if transport == "stdio":
            mcp.run()
        else:
            # Pass host/port via transport_kwargs — mcp.settings is deprecated
            # in FastMCP 2.14+ and silently ignored.
            mcp.run(transport=transport, host=host, port=port)  # type: ignore[arg-type]

    elif config.mcp_transport == "stdio":
        # Path 2: Default stdio — unchanged behavior
        log.info(
            "trw_server_initialized",
            tools_registered=True,
            debug_mode=debug,
            transport="stdio",
            mode="standalone",
        )
        mcp.run()

    else:
        # Path 3: Auto-start shared HTTP server + run as stdio proxy
        log.info(
            "trw_proxy_starting",
            target_transport=config.mcp_transport,
            target_host=config.mcp_host,
            target_port=config.mcp_port,
        )

        url = _ensure_http_server(config, log, debug=debug)

        if url is not None:
            # Run stdio proxy bridging to the shared server
            import asyncio

            try:
                asyncio.run(_run_stdio_proxy(url))
            except (KeyboardInterrupt, EOFError):
                pass  # Clean exit when Claude Code disconnects
            except ConnectionError:
                # Proxy exhausted retries — fall back to standalone
                log.warning(
                    "trw_proxy_fallback",
                    reason="proxy_connect_failed",
                    fallback="standalone_stdio",
                )
                mcp.run()
        else:
            # FR06: Fallback to standalone stdio on failure
            log.warning(
                "trw_proxy_fallback",
                reason="http_server_start_failed",
                fallback="standalone_stdio",
            )
            mcp.run()


if __name__ == "__main__":
    main()
