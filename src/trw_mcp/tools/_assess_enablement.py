"""Is the trw_assess backend enabled for this process, and which layer decided it.

A thin delegator: the one enablement precedence cascade is owned by
``trw_memory.decisions.resolve_backend_enablement`` (trw-memory must not import trw-mcp, and the
judge that makes the network call already lives there), so this module exists only to give the
MCP-side callers — the tool (``assess.py``) and the doctor row (``server/_doctor_jev.py``) — a
stable import path, and to keep the historical name (``backend_enablement``) those callers use.

Precedence (first explicit wins): process env ``TRW_JEV_ENABLED`` beats project scope
(``assess_enabled`` in the project's ``.trw/config.yaml``, or ``TRW_JEV_ENABLED`` in its ``.env``)
beats user scope (``assess_enabled`` in ``~/.trw/config.yaml``) beats off. See
``trw_memory.decisions._enablement`` for the full cascade, including the 2026-09-23 operator
decision that lets a project enable the backend (it could previously only switch it off).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

__all__ = ["assess_surfaced", "assess_surfaced_in", "backend_enablement"]


def backend_enablement(project_root: Path, env: Mapping[str, str] | None = None) -> tuple[bool, str]:
    """``(enabled, source)``. ``source`` names the deciding layer, or ``""`` when no layer said anything."""
    from trw_memory.decisions import resolve_backend_enablement

    return resolve_backend_enablement(project_root, env)


def assess_surfaced(config: object, env: Mapping[str, str] | None = None) -> bool:
    """Whether ``trw_assess`` is on the tool surface: whenever it is enabled or configured anywhere.

    Operator directive (2026-09-26): the tool is always shown once any layer turns it on. That is the
    ``assess_enabled`` field (machine then project ``.trw/config.yaml``, ``TRW_ASSESS_ENABLED``) OR
    the backend's own cascade above (``TRW_JEV_ENABLED`` in the process env or the project ``.env``).
    The backend alone used to leave the tool hidden while the judge was switched on. The surface
    resolver, the tool's own gate, the doctor row and the optional skill all
    ask this one question, for this process's own project; :func:`assess_surfaced_in` asks it for
    another directory.
    """
    if getattr(config, "assess_enabled", False) is True:
        return True
    from trw_mcp.state._paths import resolve_project_root

    return backend_enablement(resolve_project_root(), env)[0]


def assess_surfaced_in(project_root: Path) -> bool:
    """:func:`assess_surfaced` for another project's directory (an installer writing into it from elsewhere).

    Both halves are the target's own: its ``assess_enabled`` through the same machine -> project -> env
    cascade the config loader uses, and the backend cascade from its root. This process's cached config
    never decides for a different project.
    """
    import os

    from pydantic import TypeAdapter

    from trw_mcp.models.config._loader import resolve_config_overrides

    overrides = resolve_config_overrides(project_root / ".trw" / "config.yaml")
    raw = os.environ.get("TRW_ASSESS_ENABLED", overrides.get("assess_enabled", False))
    enabled = TypeAdapter(bool).validate_python(raw)
    return enabled or backend_enablement(project_root)[0]
