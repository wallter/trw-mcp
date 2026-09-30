"""CLI wiring for the shared-server verbs: ``swap``, ``status --shared``, ``env create``, and ``trw-mcp-proxy``.

The verbs belong to the ``server/_subcommands.py`` handler table (lazy verbs) and the
``server/_cli_argparse.py`` parser builder. ``main_proxy`` is its own console script.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_ENV_HELP = "env to use (default: $TRW_MCP_ENV, else stable)"


def add_shared_subcommands(subparsers: Any) -> None:
    swap = subparsers.add_parser("swap", help="point one shared env at another trw-mcp and hot-swap it")
    swap.add_argument("--env", default=None, help=_ENV_HELP)
    source = swap.add_mutually_exclusive_group()  # optional only with --daemon (checked in run_swap)
    source.add_argument("--python", type=Path, help="python, venv dir, or worktree with .venv (editable dev)")
    source.add_argument(
        "--src", type=Path, help="linked git worktree of this repo: its trw-mcp/trw-memory source, no venv"
    )
    source.add_argument("--version", dest="swap_version", help="exact version, installed offline from the wheelhouse")
    swap.add_argument(
        "--with",
        dest="with_distill",
        default=None,
        metavar="trw-distill==Y",
        help="with --version: install this trw-distill (default: the highest wheel in the wheelhouse)",
    )
    swap.add_argument(
        "--daemon",
        action="store_true",
        help="after the swap, drain this env's trw-memory daemon by handshake (alone: just drain it; "
        "the next recall starts the daemon of the env's interpreter)",
    )
    swap.add_argument("--expect-version", default=None, help="refuse unless the interpreter runs this version")
    status = subparsers.add_parser("status", help="shared-server status: `trw-mcp status --shared`")
    status.add_argument("--shared", action="store_true", required=True, help="Required: report the shared-server envs")
    env = subparsers.add_parser("env", help="per-env memory dirs for shared servers")
    env_sub = env.add_subparsers(dest="env_command", required=True)
    create = env_sub.add_parser("create", help="create an env's memory dir (grants copied, store empty)")
    create.add_argument("name")
    create.add_argument("--seed-from", default=None, help="snapshot-copy this env's store (the source is only read)")
    gc = env_sub.add_parser("gc", help="remove version venvs no env uses (dry run unless --apply)")
    gc.add_argument("--env", default=None, help=_ENV_HELP)
    gc.add_argument("--keep", type=int, default=2, help="newest version venvs always kept (default 2, minimum 1)")
    gc.add_argument("--apply", action="store_true", help="remove them; without it, only print what would go")


def _env(args: argparse.Namespace) -> str:
    from trw_mcp.shared_server._records import STABLE, validate_env

    return validate_env(getattr(args, "env", None) or os.environ.get("TRW_MCP_ENV") or STABLE)


def shared_project_root() -> Path:
    """The TRW project the shared-server verbs act on (SWAP-PROJECT-ROOT), or a named refusal.

    ``_project_root.trw_project_root`` is the one resolver, shared with the proxy (PROXY-PROJECT-ROOT). A verb
    outside a TRW project refuses: no record root is ever created there silently.
    """
    from trw_mcp.shared_server._project_root import trw_project_root
    from trw_mcp.shared_server._records import SharedServerError
    from trw_mcp.state._paths import resolve_project_root

    root = trw_project_root()
    if root is None:
        raise SharedServerError(
            f"not a TRW project: {resolve_project_root()} (no .trw/config.yaml at it or its git toplevel); "
            "run from inside a TRW project or set TRW_PROJECT_ROOT to one"
        )
    return root


def _paths() -> tuple[Any, Any, Path]:
    """(paths, config, project root) for a shared-server verb; refuses (exit 1) outside a TRW project."""
    from trw_mcp.models.config import get_config
    from trw_mcp.shared_server._records import SharedPaths, SharedServerError

    try:
        root = shared_project_root()
    except SharedServerError as exc:
        print(f"trw-mcp: {exc}", file=sys.stderr)
        sys.exit(1)
    from trw_mcp.state._project_root_binding import project_bound

    with project_bound(root):  # the root's own .trw/config.yaml, whatever subdirectory resolved to it
        config = get_config()
    return SharedPaths.resolve(root / str(config.trw_dir), config.shared_mcp), config, root


def _refusing(action: Any) -> None:
    from trw_mcp.shared_server._records import SharedServerError

    try:
        action()
    except SharedServerError as exc:
        print(f"trw-mcp: {exc}", file=sys.stderr)
        sys.exit(1)


def _enabled_at(root: Path) -> bool:
    """``shared_mcp.enabled`` from the project root's own config (not a subdirectory's)."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._project_root_binding import project_bound

    with project_bound(root):
        return bool(get_config().shared_mcp.enabled)


def main_proxy() -> None:
    """``trw-mcp-proxy [--env X]``: the stdio entry a client launches (its own lean console script).

    It never imports the MCP app, so each client session costs a small process. It becomes
    ``trw-mcp serve`` in-process when ``shared_mcp.enabled`` is false (the rollback lever) and
    for a bounded lane: a reviewer role or dispatched child is enforced by the serving process's
    own environment, which a shared server does not have.
    """
    from trw_mcp.dispatch._child_marker import dispatched_child_active
    from trw_mcp.state._surface_role import reviewer_role_active

    parser = argparse.ArgumentParser(prog="trw-mcp-proxy", description="stdio shim to the shared trw-mcp")
    parser.add_argument("--env", default=None, help=_ENV_HELP)
    args = parser.parse_args()
    from trw_mcp.shared_server._project_root import trw_project_root

    root = None if reviewer_role_active() or dispatched_child_active() else trw_project_root()
    stdio_reason = (
        "bounded lane (reviewer role or dispatched child)"
        if reviewer_role_active() or dispatched_child_active()
        # PROXY-PROJECT-ROOT: outside a TRW project there is no shared record to use, and none is ever made.
        else f"not inside a TRW project ({Path.cwd()})"
        if root is None
        else None
        if _enabled_at(root)
        else "shared_mcp.enabled is false"
    )
    if stdio_reason is not None:
        print(f"trw-mcp-proxy: {stdio_reason}; serving stdio in-process", file=sys.stderr)
        from trw_mcp.server._cli import main

        sys.argv = ["trw-mcp", "serve"]
        main()
        return
    from trw_mcp.shared_server._proxy import run_proxy

    _refusing(lambda: run_proxy(_env(args)))


def run_swap(args: argparse.Namespace) -> None:
    from trw_mcp.shared_server._ops import (
        build_version_venv,
        drain_env_daemon,
        resolve_python,
        swap,
        worktree_pythonpath,
    )
    from trw_mcp.shared_server._records import SharedServerError

    paths, config, project_root = _paths()

    def act() -> None:
        env = _env(args)
        if args.with_distill is not None and args.swap_version is None:
            raise SharedServerError("--with only applies to --version (it picks what the new venv installs)")
        pythonpath = None
        no_source = args.python is None and getattr(args, "src", None) is None and args.swap_version is None
        if no_source and not args.daemon:
            raise SharedServerError("swap needs one of --python, --src, --version (or --daemon alone to drain)")
        if no_source:
            if args.expect_version:
                raise SharedServerError(
                    "--expect-version checks the swapped interpreter; it does not apply to --daemon alone"
                )
            print(drain_env_daemon(paths, env, project_root=project_root))
            return
        if getattr(args, "src", None) is not None:
            # This interpreter (the one `trw-mcp swap` runs on) with the worktree's source first on the path.
            python, pythonpath = Path(sys.executable), worktree_pythonpath(args.src, project_root)
        elif args.python is not None:
            python = resolve_python(args.python)
        else:
            python = build_version_venv(
                paths, env, args.swap_version, config.shared_mcp, with_distill=args.with_distill
            )
        expect = args.expect_version or args.swap_version
        print(swap(paths, env, python, project_root=project_root, expect=expect, pythonpath=pythonpath))
        print(f"project root {project_root} · record {paths.record(env)}")  # which record the server pid lives in
        if args.daemon:
            try:
                print(drain_env_daemon(paths, env, project_root=project_root))
            except SharedServerError as exc:
                raise SharedServerError(f"swap succeeded; daemon drain refused: {exc}") from exc

    _refusing(act)


def run_status(args: argparse.Namespace) -> None:
    from trw_mcp.shared_server._ops import status_rows

    paths, _config, _root = _paths()
    _refusing(lambda: print(json.dumps(status_rows(paths), indent=2)))


def run_env(args: argparse.Namespace) -> None:
    from trw_mcp.shared_server._ops import ensure_env
    from trw_mcp.shared_server._records import serving_env_path

    paths, _config, _root = _paths()
    if getattr(args, "env_command", "create") == "gc":
        from trw_mcp.shared_server._gc import run_gc

        _refusing(
            lambda: print("\n".join(run_gc(paths, _env(args), keep=args.keep, apply=args.apply)) or "nothing to do")
        )
        return

    def act() -> None:
        from trw_mcp.shared_server._ops import _memory_dir

        existed = args.name != "stable" and _memory_dir(paths, args.name).is_dir()
        created = ensure_env(paths, args.name, seed_from=args.seed_from)
        # A (re)created env forgets the serving environment recorded for its previous life; swap never clears it.
        serving_env_path(paths, args.name).unlink(missing_ok=True)
        # INC-126 (e): an env that was already there is not "created" again.
        print(f"exists {created} (grants refreshed)" if existed else f"created {created}")

    _refusing(act)


def run_shared_serve(args: argparse.Namespace) -> None:
    """``trw-mcp serve --shared`` (after the ordinary boot sequence ran in ``_serve``)."""
    from trw_mcp.shared_server._server import serve_shared

    _refusing(lambda: serve_shared(env=_env(args), successor=bool(args.successor)))
