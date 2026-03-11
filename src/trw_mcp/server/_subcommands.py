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
    import json as _json
    import urllib.request

    url = f"{backend_url.rstrip('/')}/v1/releases"
    payload = _json.dumps({
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
            data = _json.loads(resp.read().decode("utf-8"))
            print(f"  Published: v{data.get('version', '?')} to {backend_url}")
    except Exception as exc:
        print(f"  ERROR publishing: {exc}")
        sys.exit(1)


def _get_framework_version() -> str:
    """Extract framework version from bundled FRAMEWORK.md."""
    from trw_mcp.state._helpers import read_framework_version
    return read_framework_version()


SUBCOMMAND_HANDLERS: dict[str, Callable[[argparse.Namespace], None]] = {
    "init-project": _run_init_project,
    "update-project": _run_update_project,
    "audit": _run_audit,
    "export": _run_export,
    "import-learnings": _run_import_learnings,
    "build-release": _run_build_release,
}
