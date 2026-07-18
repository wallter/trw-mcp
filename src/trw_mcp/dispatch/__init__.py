"""Cross-client dispatch layer (public, BSL-1.1).

Lets a shell-capable agent (e.g. Claude Code) run ANOTHER coding-agent CLI
headlessly and capture its output — for independent second-opinion audits.
Additive: this never changes existing TRW behavior.

Public API:
    dispatch          — run a DispatchRequest, return a DispatchResult
    build_command     — pure argv builder for a request
    apply_role        — prepend a read-only audit role preamble to a prompt
    DispatchRequest   — typed launch request
    DispatchResult    — normalized, prompt-redacted result
    DispatchClient    — Literal of supported clients
    SUPPORTED_CLIENTS — tuple of supported client ids
    ROLE_TEMPLATES    — audit-role preambles
"""

from __future__ import annotations

from trw_mcp.dispatch._commands import SUPPORTED_CLIENTS, build_command
from trw_mcp.dispatch._roles import ROLE_TEMPLATES, apply_role
from trw_mcp.dispatch._runner import dispatch
from trw_mcp.dispatch._types import DispatchClient, DispatchRequest, DispatchResult

__all__ = [
    "ROLE_TEMPLATES",
    "SUPPORTED_CLIENTS",
    "DispatchClient",
    "DispatchRequest",
    "DispatchResult",
    "apply_role",
    "build_command",
    "dispatch",
]
