"""The ``jev`` row of ``trw-mcp doctor``: is ``trw_assess`` usable here, and if not, what is missing.

Belongs to the ``_subcommands_doctor.py`` catalogue; kept in a sibling for the eLOC gate. It works
the same under every client, which is the point: an operator on Codex, Cursor, AGY or Grok checks
the setup here, not in a client-specific file. It reports where each input comes from and never
the key itself.
"""

from __future__ import annotations

import os
from pathlib import Path

from trw_mcp.models.config import TRWConfig

__all__ = ["jev_row"]

_ENABLE_HINT = "add `assess_enabled: true` to ~/.trw/config.yaml (or the project's .trw/config.yaml)"


def _key_source(target: Path) -> str:
    if os.environ.get("OPENROUTER_API_KEY"):
        return "environment"
    from trw_memory.decisions._dotenv import parse_dotenv_subset

    if parse_dotenv_subset(target / ".env", allowed_keys=frozenset({"OPENROUTER_API_KEY"})):
        return "project .env"
    return ""


def jev_row(target: Path, config: TRWConfig) -> tuple[str, str]:
    """``(status, message)``: SKIP when off, WARN when shown but unusable, PASS when usable."""
    from trw_mcp.tools._assess_enablement import backend_enablement

    visible = bool(getattr(config, "assess_enabled", False))
    enabled, source = backend_enablement(target)
    key = _key_source(target)
    if not visible and not enabled:
        return "SKIP", f"trw_assess off (experimental, opt-in): {_ENABLE_HINT}"
    # A layer that said no is named, so an operator can see which switch turned it off.
    off_reason = f"switched off by {source}" if source else _ENABLE_HINT
    missing = [
        *([] if visible else ["tool hidden (assess_enabled false for this project)"]),
        *([] if enabled else [f"backend off: {off_reason}"]),
        *([] if key else ["no OPENROUTER_API_KEY in the environment or the project .env"]),
    ]
    if missing:
        return "WARN", "trw_assess shown but every call returns disabled: " + "; ".join(missing)
    return "PASS", f"trw_assess usable: backend enabled by {source}, key from {key}"
