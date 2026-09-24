"""A checkout with nothing to move starts migrated (PRD-CORE-280 FR06).

``init-project`` and ``update-project`` pin ``project_namespace`` and mint the
checkout's daemon grant when the project ``memory.db`` holds no learning row
(absent, empty, or canary decoys only). A checkout whose ``memory.db`` holds
rows, pinned or not, keeps its config unchanged, gets no grant, and gets one
line naming ``trw-mcp memory migrate --to user --apply`` -- which also merges a pinned
checkout's strays into its namespace.

A grant is minted only for a pin that equals the namespace derived from the
checkout itself, as ``trw-mcp memory token`` does without ``--namespace``, so no
install mints another project's grant as a side effect.
"""

from __future__ import annotations

from pathlib import Path


def store_holds_data(target_dir: Path) -> bool:
    """Whether *target_dir*'s own project ``memory.db`` holds a learning row (``memory migrate``'s own test)."""
    from trw_mcp.state._store_migration import holds_rows

    return holds_rows(target_dir / ".trw" / "memory" / "memory.db")


def written_pin(target_dir: Path) -> str | None:
    """The ``project_namespace`` that *target_dir*'s config.yaml itself records -- no env overlay."""
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    path = target_dir / ".trw" / "config.yaml"
    if not path.is_file():
        return None
    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except YAMLError:  # trw-fail-silent-allow: an unparseable config records no pin; --force replaces it
        return None
    value = data.get("project_namespace") if isinstance(data, dict) else None
    return value if isinstance(value, str) and value else None


def pin_empty_checkout(target_dir: Path, result: dict[str, list[str]]) -> None:
    """Pin and grant *target_dir* when it has no project store to move; otherwise name the migration."""
    from trw_memory.daemon import DaemonPaths, mint_grant, write_checkout_grant
    from trw_memory.daemon._grants import CHECKOUT_TOKEN_RELPATH
    from trw_memory.namespaces.identity import resolve_project_namespace

    from trw_mcp.exceptions import StateError
    from trw_mcp.state._store_migration import _pin, _set_pin
    from trw_mcp.state._tier_routing import USER_NAMESPACE

    # Checked before pinning AND before minting: a pinned checkout whose own
    # store still holds rows is a split store, and a grant would hide it.
    if store_holds_data(target_dir):
        result.setdefault("warnings", []).append(
            f"{target_dir / '.trw' / 'memory' / 'memory.db'} holds learnings; run "
            "`trw-mcp memory migrate --to user --apply` to move them to the shared store (without --apply it only previews)"
        )
        return
    trw_dir = target_dir / ".trw"
    derived = resolve_project_namespace(target_dir)
    try:
        pinned = _pin(trw_dir)
    except StateError:  # trw-fail-silent-allow: update-project fails open on a corrupt config; the warning names it
        result.setdefault("warnings", []).append(
            f"{trw_dir / 'config.yaml'} does not parse; project_namespace was not pinned and no memory grant was minted"
        )
        return
    if not pinned:
        _set_pin(trw_dir, derived)
        pinned = derived
    if pinned != derived or (target_dir / CHECKOUT_TOKEN_RELPATH).is_file():
        return
    write_checkout_grant(trw_dir, mint_grant(DaemonPaths.resolve(), {pinned, USER_NAMESPACE}, root=target_dir))
