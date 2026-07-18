"""Back-compat shim — the implementation moved to ``state/_evidence_gates.py``.

Receipt validation is a state-layer concern (PRD-FIX-061-FR07): the state
layer must never import from ``tools/``, and ``state/_trust_receipts.py``
consumes these validators. This module re-exports the same public API from its
new home so existing tool-layer importers keep working unchanged.
"""

from __future__ import annotations

from trw_mcp.state._evidence_gates import (
    read_evidence_mode,
    select_typed_review_state,
    validate_build_receipt,
    validate_review_receipt,
    validate_verification_receipt,
)

__all__ = [
    "read_evidence_mode",
    "select_typed_review_state",
    "validate_build_receipt",
    "validate_review_receipt",
    "validate_verification_receipt",
]
