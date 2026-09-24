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


def _run_memory_migrate(args: argparse.Namespace) -> None:
    import json

    from trw_mcp.state._store_migration import (
        DaemonRunningError,
        MigrationRefusedError,
        apply_migration,
        preview_migration,
        rollback_migration,
    )

    trw_dir = Path(args.target_dir).resolve() / ".trw"
    try:
        if args.rollback:
            restored = rollback_migration(trw_dir, Path(args.rollback))
            print(f"memory migrate: rolled back; {restored} rows restored under default")
        elif args.apply:
            print(f"memory migrate: migrated; manifest {apply_migration(trw_dir)}")
        else:
            print(json.dumps(preview_migration(trw_dir), indent=2))
    except MigrationRefusedError as exc:
        print(f"memory migrate: {exc}", file=sys.stderr)
        sys.exit(2 if isinstance(exc, DaemonRunningError) else 1)


def run_memory(args: argparse.Namespace) -> None:
    """Dispatch ``memory <subcommand>``."""
    command = getattr(args, "memory_command", None)
    if command in {"token", "migrate"}:
        (_run_memory_token if command == "token" else _run_memory_migrate)(args)
        return
    print(
        "Usage: trw-mcp memory token [--namespace PINNED] [--grant NAMESPACE]... [--migrate]\n"
        "       trw-mcp memory migrate --to user [--apply | --rollback MANIFEST]",
        file=sys.stderr,
    )
    sys.exit(2)
