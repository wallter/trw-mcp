"""``trw-mcp uninstall --delete-memory`` -- forget this checkout's own namespace (PRD-CORE-280 FR06).

The memory daemon's one store at ``~/.trw`` holds every checkout's namespace and
the machine's ``user:local``, so uninstall never removes that directory or its
database. ``--delete-memory`` pages the checkout's pinned ``project_namespace``
through the checkout's own grant and forgets each row, which the daemon allows
only inside a namespace the grant covers.

Authority comes from the target alone: the pin written in the target's own
``.trw/config.yaml`` (no env or machine overlay) and the token in the target's
own ``.trw/runtime/memory-token`` (no walk up to a parent checkout), whose grant
must be rooted at the target and cover the pin. Anything else -- ``user:local``,
no pin, no token, a grant minted for another checkout or not covering the pin --
is refused before anything is deleted. Canary decoys are the store's own guards
and stay.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, NamedTuple

_PAGE = 500


class MemoryDeleteRefusedError(RuntimeError):
    """This checkout's namespace cannot be (fully) deleted; the message says how much was."""


class CheckoutMemory(NamedTuple):
    """What ``--delete-memory`` may delete: one namespace, and the grant that covers it."""

    namespace: str
    token: str


def shared_store() -> Path:
    """Where the daemon's one store lives; uninstall reports it and never removes it."""
    from trw_memory.daemon import DaemonPaths

    return DaemonPaths.resolve(create=False).store


def checkout_memory(target: Path) -> CheckoutMemory:
    """The namespace *target* itself pins and grants, or refuse; nothing is deleted here."""
    from trw_memory.daemon import DaemonPaths
    from trw_memory.daemon._grants import CHECKOUT_TOKEN_RELPATH, read_grant
    from trw_memory.daemon._paths import read_secret_file
    from trw_memory.exceptions import DaemonSecretUnreadableError

    from trw_mcp.bootstrap._namespace_pin import written_pin
    from trw_mcp.state._tier_routing import USER_NAMESPACE

    pinned = written_pin(target)
    if not pinned:
        raise MemoryDeleteRefusedError(
            f"{target / '.trw' / 'config.yaml'} pins no project_namespace; nothing to delete"
        )
    if pinned == USER_NAMESPACE:
        raise MemoryDeleteRefusedError(f"{USER_NAMESPACE} is shared by every checkout on this machine; not deleting it")
    token_path = target / CHECKOUT_TOKEN_RELPATH
    try:
        token = (read_secret_file(token_path) or "").strip()
    except DaemonSecretUnreadableError as exc:
        raise MemoryDeleteRefusedError(f"{token_path} cannot be read ({exc})") from exc
    if not token:
        raise MemoryDeleteRefusedError(f"{target} has no memory grant of its own ({token_path})")
    grant = read_grant(DaemonPaths.resolve(create=False), token)
    if grant is None or grant.root != str(target.resolve()):
        raise MemoryDeleteRefusedError(f"the grant in {token_path} was not minted for {target}")
    if pinned not in grant.namespaces:
        raise MemoryDeleteRefusedError(f"this checkout's grant does not cover {pinned}")
    return CheckoutMemory(pinned, token)


def delete_checkout_memory(memory: CheckoutMemory) -> int:
    """Forget every non-canary row of *memory*'s namespace through its grant; returns the rows deleted."""
    from trw_memory.daemon import DaemonPaths
    from trw_memory.daemon.client import DaemonClient

    client = DaemonClient(memory.token, paths=DaemonPaths.resolve(create=False))
    return asyncio.run(_forget_all(client, memory.namespace))


async def _forget_all(client: Any, namespace: str) -> int:
    # Every id is collected before the first forget, so deleting cannot shift a page.
    ids: list[str] = []
    after: dict[str, str] | None = None
    while True:
        page = _ok(await client.list_page(namespace, _PAGE, after))
        ids += [row["id"] for row in page["entries"] if row.get("metadata", {}).get("system_canary") != "true"]
        if not (after := page.get("next")):
            break
    deleted = 0
    for entry_id in ids:
        try:
            deleted += int(_ok(await client.forget(entry_id, namespace)).get("deleted", 0))
        except (MemoryDeleteRefusedError, OSError) as exc:
            raise MemoryDeleteRefusedError(
                f"deleted {deleted} of {len(ids)} row(s) of {namespace}, then stopped: {exc}; re-run to delete the rest"
            ) from exc
    return deleted


def _ok(answer: dict[str, Any]) -> dict[str, Any]:
    if answer.get("status") != "ok":
        raise MemoryDeleteRefusedError(f"the memory daemon refused: {answer.get('error')}")
    return answer
