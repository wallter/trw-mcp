"""Back-compat shim — the implementation moved to ``state/_evidence_binding.py``.

The stable-read / scope-minting / content-binding primitives are a state-layer
persistence/validation concern (PRD-FIX-061-FR07): the state layer must never
import from ``tools/``. This module re-exports the same public API from its new
home so existing tool-layer importers keep working unchanged.
"""

from __future__ import annotations

from trw_mcp.state._evidence_binding import (
    BindingOutcome,
    StableReadError,
    build_content_binding,
    content_binding_is_current,
    mint_run_owned_scope,
    read_content_entry,
)

__all__ = [
    "BindingOutcome",
    "StableReadError",
    "build_content_binding",
    "content_binding_is_current",
    "mint_run_owned_scope",
    "read_content_entry",
]
