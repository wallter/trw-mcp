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
        _print_cli_line("Codex: managed config, hooks, agents, skills, and AGENTS.md synced")
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


def _run_build_release(args: argparse.Namespace) -> None:
    """Handle the ``build-release`` subcommand."""
    from trw_mcp.release_builder import build_release_bundle

    version: str | None = getattr(args, "version", None)
    output_dir = Path(getattr(args, "output_dir", ".")).resolve()

    result = build_release_bundle(version=version, output_dir=output_dir)

    logger.info(
        "build_release_complete",
        op="build_release",
        bundle_path=str(result["path"]),
        version=str(result["version"]),
        checksum=str(result["checksum"]),
        size_bytes=result["size_bytes"],
    )

    push = getattr(args, "push", False)
    if push:
        backend_url = getattr(args, "backend_url", None)
        api_key = getattr(args, "api_key", None)
        if not backend_url or not api_key:
            logger.error("push_missing_args", op="build_release", detail="--push requires --backend-url and --api-key")
            sys.exit(1)
        _push_release(result, backend_url, api_key)

    sys.exit(0)


def _push_release(result: dict[str, object], backend_url: str, api_key: str) -> None:
    """Push release metadata to the backend."""
    import json as _json
    import urllib.request

    url = f"{backend_url.rstrip('/')}/v1/releases"
    payload = _json.dumps(
        {
            "version": str(result["version"]),
            "artifact_url": str(result["path"]),
            "artifact_checksum": str(result["checksum"]),
            "artifact_size_bytes": int(str(result["size_bytes"])),
            "framework_version": _get_framework_version(),
        }
    ).encode("utf-8")

    req = urllib.request.Request(  # noqa: S310 — URL comes from CLI --backend-url arg (operator-supplied, not end-user input); HTTPS enforced by deployment
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 — see Request comment above
            data = _json.loads(resp.read().decode("utf-8"))
            logger.info(
                "release_published",
                op="push_release",
                version=data.get("version", "?"),
                backend_url=backend_url,
            )
    except Exception as exc:  # justified: boundary, backend publish API call may fail
        logger.exception("release_publish_failed", op="push_release", error=str(exc))
        sys.exit(1)


def _get_framework_version() -> str:
    """Extract framework version from bundled FRAMEWORK.md."""
    from trw_mcp.state._helpers import read_framework_version

    return read_framework_version()


def _run_uninstall(args: argparse.Namespace) -> None:
    """Handle the ``uninstall`` subcommand -- remove TRW files from a project."""
    import shutil

    target = Path(getattr(args, "target_dir", ".")).resolve()
    dry_run: bool = getattr(args, "dry_run", False)
    yes: bool = getattr(args, "yes", False)

    # Files and directories created by init-project
    paths_to_remove: list[Path] = [
        target / ".trw",
        target / ".mcp.json",
        target / ".claude" / "skills",
        target / ".claude" / "agents",
        target / ".claude" / "hooks",
    ]

    # Find what exists
    existing = [p for p in paths_to_remove if p.exists()]

    if not existing:
        print("  No TRW files found in this project.")
        return

    print(f"\n  TRW files found in {target}:\n")
    for p in existing:
        kind = "dir " if p.is_dir() else "file"
        size = ""
        if p.is_dir():
            count = sum(1 for _ in p.rglob("*") if _.is_file())
            size = f" ({count} files)"
        print(f"    {kind}  {p.relative_to(target)}{size}")

    if dry_run:
        print("\n  --dry-run: no files removed.")
        return

    if not yes:
        print()
        confirm = input("  Remove these files? [y/N] ").strip().lower()
        if confirm not in ("y", "yes"):
            print("  Aborted.")
            return

    # Remove
    removed = 0
    for p in existing:
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed += 1
            print(f"  Removed: {p.relative_to(target)}")
        except OSError as exc:  # noqa: PERF203
            print(f"  Error removing {p.relative_to(target)}: {exc}")

    print(f"\n  Done. Removed {removed} item(s).")


def _run_auth(args: argparse.Namespace) -> None:
    """Handle the ``auth`` subcommand (login/logout/status)."""
    from trw_mcp.cli.auth import run_auth_login, run_auth_logout, run_auth_status

    config_path = Path.cwd() / ".trw" / "config.yaml"
    api_url = getattr(args, "api_url", None) or "https://api.trwframework.com"

    auth_cmd = getattr(args, "auth_command", None)
    if auth_cmd == "login":
        sys.exit(run_auth_login(api_url, config_path))
    elif auth_cmd == "logout":
        sys.exit(run_auth_logout(config_path))
    elif auth_cmd == "status":
        sys.exit(run_auth_status(config_path, api_url))
    else:
        # No auth subcommand: show help
        print("Usage: trw-mcp auth {login|logout|status}")
        print()
        print("Commands:")
        print("  login   Authenticate via device authorization flow")
        print("  logout  Remove stored API key")
        print("  status  Show current authentication status")
        sys.exit(0)


def _run_config_reference(args: argparse.Namespace) -> None:
    """Handle the ``config-reference`` subcommand -- print config env vars."""
    from trw_mcp.models.config._main_fields import _TRWConfigFields

    print("# TRW Configuration Reference\n")
    print("All values can be set via environment variables with `TRW_` prefix.\n")
    print("| Environment Variable | Type | Default | Description |")
    print("|---------------------|------|---------|-------------|")

    for name, field_info in _TRWConfigFields.model_fields.items():
        env_var = f"TRW_{name.upper()}"
        annotation = field_info.annotation
        field_type = (
            str(annotation)
            .replace("typing.", "")
            .replace("<class '", "")
            .replace("'>", "")
        )
        default = field_info.default if field_info.default is not None else ""
        # Truncate long defaults
        default_str = str(default)
        if len(default_str) > 40:
            default_str = default_str[:37] + "..."
        desc = field_info.description or ""
        print(f"| `{env_var}` | {field_type} | `{default_str}` | {desc} |")


SUBCOMMAND_HANDLERS: dict[str, Callable[[argparse.Namespace], None]] = {
    "init-project": _run_init_project,
    "update-project": _run_update_project,
    "audit": _run_audit,
    "export": _run_export,
    "import-learnings": _run_import_learnings,
    "build-release": _run_build_release,
    "auth": _run_auth,
    "uninstall": _run_uninstall,
    "config-reference": _run_config_reference,
}
