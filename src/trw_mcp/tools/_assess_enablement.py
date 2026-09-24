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

__all__ = ["backend_enablement"]


def backend_enablement(project_root: Path, env: Mapping[str, str] | None = None) -> tuple[bool, str]:
    """``(enabled, source)``. ``source`` names the deciding layer, or ``""`` when no layer said anything."""
    from trw_memory.decisions import resolve_backend_enablement

    return resolve_backend_enablement(project_root, env)
