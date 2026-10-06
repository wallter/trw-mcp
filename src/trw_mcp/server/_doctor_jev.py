"""The ``jev`` row of ``trw-mcp doctor``: is ``trw_assess`` usable here, and if not, what is missing.

Belongs to the ``_subcommands_doctor.py`` catalogue; kept in a sibling for the eLOC gate. It works
the same under every client, which is the point: an operator on Codex, Cursor, AGY or Grok checks
the setup here, not in a client-specific file. It reports where each input comes from and never
the key itself.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from trw_memory.decisions._machine_store import MACHINE_STORE_LABEL

from trw_mcp.models.config import TRWConfig

__all__ = ["jev_row"]

_ENABLE_HINT = "add `assess_enabled: true` to ~/.trw/config.yaml (or the project's .trw/config.yaml)"


def jev_row(target: Path, config: TRWConfig) -> tuple[Literal["PASS", "WARN", "SKIP"], str]:
    """``(status, message)``: SKIP when off, WARN when shown but unusable, PASS when usable.

    Names the layer that enabled the backend and the layer that supplied the key (environment,
    project .env or the ``~/.trw/jev.env`` machine store), never the key itself.
    """
    from trw_memory.decisions import resolve_jev_settings

    from trw_mcp.tools._assess_enablement import backend_enablement

    enabled, source = backend_enablement(target)
    settings = resolve_jev_settings(dict(os.environ), target / ".env")
    # The tool is shown whenever either switch is on (``assess_surfaced``), so "off" means both are.
    if not getattr(config, "assess_enabled", False) and not enabled:
        return "SKIP", f"trw_assess off (experimental, opt-in): {_ENABLE_HINT}"
    # A layer that said no is named, so an operator can see which switch turned it off.
    off_reason = f"switched off by {source}" if source else _ENABLE_HINT
    store_note = f" ({MACHINE_STORE_LABEL} refused: it {settings.store_problem})" if settings.store_problem else ""
    missing = [
        *([] if enabled else [f"backend off: {off_reason}"]),
        *(
            []
            if settings.api_key
            else [f"no OPENROUTER_API_KEY in the environment, the project .env or {MACHINE_STORE_LABEL}{store_note}"]
        ),
    ]
    if missing:
        return "WARN", "trw_assess shown but every call returns disabled: " + "; ".join(missing)
    return "PASS", f"trw_assess usable: backend enabled by {source}, key from {settings.key_source}{store_note}"
