"""``trw-mcp memory token`` -- mint this checkout's daemon grant (PRD-CORE-298 FR02).

``trw-mcp memory migrate`` (PRD-CORE-280 FR03) is ``state/_store_migration.py``
behind a thin dispatcher here; it mints the same checkout grant as it migrates.

A daemon token reaches only the namespaces it was granted. This verb grants
exactly what the checkout owns: its
project namespace and ``user:local``. ``--grant`` can only narrow that set;
naming any other namespace exits non-zero before the grants file is touched.

The namespace is always read from the target's own ``.trw/config.yaml`` --
never through the env-shadowed config cascade -- so a ``TRW_PROJECT_NAMESPACE``
override in the operator's shell can never mint a grant for a namespace this
checkout's own file does not name; a disagreeing override exits non-zero
before anything is minted.

Without ``--namespace`` the verb grants the pinned ``project_namespace`` only
when it equals the identity derived from the checkout itself
(``resolve_project_identity`` of the resolved ``--target-dir``). A pin that
differs -- a checkout moved after ``memory migrate``, or a pin edited by
mistake -- is granted only when the operator names it with ``--namespace``. The
explicit flag is the point: nothing mints another project's grant as a side
effect. ``--target-dir`` names the checkout being granted, so pointing it at
another checkout is itself an explicit request for that checkout's grant.

This is accident prevention, not isolation. Every agent runs as the same OS
user as the daemon and can read the store and the grants file directly; see
PRD-CORE-298 FR02.

``--migrate`` retires the Slice A all-namespace ``daemon-token`` (the daemon
refuses to start while it exists) and then mints this checkout's grant, and
nothing for any other project.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

__all__ = ["run_memory"]


def _owned_namespaces(target: Path, named: str | None) -> frozenset[str]:
    import os

    from trw_memory.namespaces.identity import resolve_project_identity

    from trw_mcp.bootstrap._namespace_pin import written_pin
    from trw_mcp.state._tier_routing import USER_NAMESPACE

    # written_pin reads config.yaml directly -- no env overlay -- so a
    # TRW_PROJECT_NAMESPACE override in the operator's shell can never mint a
    # grant for a namespace this checkout's own file does not name.
    pinned = written_pin(target)
    if not pinned:
        sys.exit(f"memory token: {target} has no pinned project_namespace; an unmigrated checkout needs no grant")
    env_namespace = os.environ.get("TRW_PROJECT_NAMESPACE")
    if env_namespace is not None and env_namespace != pinned:
        sys.exit(
            f"memory token: TRW_PROJECT_NAMESPACE={env_namespace} disagrees with {target}'s pinned "
            f"project_namespace {pinned}; refusing to mint. Unset the override, or name the pin explicitly: "
            f"trw-mcp memory token --target-dir {target} --namespace {pinned}"
        )
    if named is not None and named != pinned:
        sys.exit(f"memory token: --namespace {named} is not {target}'s pinned project_namespace {pinned}")
    derived = resolve_project_identity(target).namespace
    if named is None and pinned != derived:
        sys.exit(
            f"memory token: {target} is pinned to {pinned}, but its location derives {derived}; nothing was "
            f"minted. If the checkout moved, grant the pin explicitly: "
            f"trw-mcp memory token --target-dir {target} --namespace {pinned}"
        )
    return frozenset({pinned, USER_NAMESPACE})


def _run_memory_token(args: argparse.Namespace) -> None:
    from trw_memory.daemon import DaemonPaths, mint_grant, write_checkout_grant

    target = Path(args.target_dir).resolve()
    owned = _owned_namespaces(target, args.namespace)
    requested = frozenset(args.grant or owned)
    if not requested <= owned:
        sys.exit(
            f"memory token: refusing {sorted(requested - owned)}; this checkout may be granted only {sorted(owned)}"
        )
    paths = DaemonPaths.resolve()
    if args.migrate and paths.token.exists():
        paths.token.unlink()
        print(f"memory token: removed the Slice A all-namespace token {paths.token}")
    written = write_checkout_grant(target / ".trw", mint_grant(paths, requested, root=target))
    print(f"memory token: granted {sorted(requested)}; token stored at {written}")


def _reembed_line(answer: dict[str, object]) -> str:
    if answer.get("status") != "ok":
        fix = f"; fix: {answer['fix']}" if answer.get("fix") else ""
        return f"vectors not re-embedded ({answer.get('reason') or answer.get('error')}){fix}"
    return (
        f"re-embedded {answer['reembedded']} of {answer['examined']} rows; "
        f"{answer['outside_active_space']} still outside the active space"
    )


def _run_memory_migrate(args: argparse.Namespace) -> None:
    import json

    from trw_mcp.state import _checkout_servers
    from trw_mcp.state._store_migration import (
        MigrationRefusedError,
        MigrationRetryError,
        apply_migration,
        preview_migration,
        reembed_checkout,
        rollback_migration,
    )

    trw_dir = Path(args.target_dir).resolve() / ".trw"
    try:
        if args.rollback:
            restored = rollback_migration(trw_dir, Path(args.rollback))
            print(f"memory migrate: rolled back; {restored} rows restored under default")
            if missing := json.loads(Path(args.rollback).read_text(encoding="utf-8")).get("rollback_vectors_missing"):
                print(
                    f"memory migrate: {len(missing)} rows came back without vectors (another embedding space, "
                    f"never re-embedded): run `trw-mcp memory reembed` to rebuild them: {', '.join(missing)}"
                )
        elif args.apply:
            manifest = apply_migration(trw_dir)
            print(f"memory migrate: migrated; manifest {manifest}")
            print(f"memory migrate: {_reembed_line(reembed_checkout(trw_dir))}")
            for line in _checkout_servers.live_servers(trw_dir):
                print(f"memory migrate: still running on the old store, reconnect: {line}")
        else:
            print(json.dumps(preview_migration(trw_dir), indent=2))
    except MigrationRefusedError as exc:
        # Exit 2 is "stop something, then rerun": say what still runs against this checkout.
        print(f"memory migrate: {exc}", file=sys.stderr)
        retry = isinstance(exc, MigrationRetryError)
        for line in _checkout_servers.live_servers(trw_dir) if retry else ():
            print(f"memory migrate: still running against this checkout: {line}", file=sys.stderr)
        sys.exit(2 if retry else 1)


def _run_memory_reembed(args: argparse.Namespace) -> None:
    """Exit 1 only when the daemon refused the request as invalid; other outcomes are the ``status``."""
    import json

    from trw_mcp.state._store_migration import reembed_checkout

    answer = reembed_checkout(Path(args.target_dir).resolve() / ".trw")
    print(json.dumps(answer, default=str) if args.as_json else f"memory reembed: {_reembed_line(answer)}")
    sys.exit(1 if answer.get("status") == "invalid" else 0)


def run_memory(args: argparse.Namespace) -> None:
    """Dispatch ``memory <subcommand>``."""
    command = getattr(args, "memory_command", None)
    handlers = {"token": _run_memory_token, "migrate": _run_memory_migrate, "reembed": _run_memory_reembed}
    if command in handlers:
        handlers[command](args)
        return
    print(
        "Usage: trw-mcp memory token [--namespace PINNED] [--grant NAMESPACE]... [--migrate]\n"
        "       trw-mcp memory migrate --to user [--apply | --rollback MANIFEST]\n"
        "       trw-mcp memory reembed [--json]",
        file=sys.stderr,
    )
    sys.exit(2)
