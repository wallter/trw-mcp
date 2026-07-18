"""Back-compat shim — implementation moved to ``state/_evidence_persistence.py``.

Durable receipt persistence is a state-layer concern (PRD-FIX-061-FR07): the
state layer must never import from ``tools/``. This module re-exports the same
public API from its new home so existing tool-layer importers keep working
unchanged.
"""

from __future__ import annotations

from trw_mcp.state._evidence_persistence import (
    WriteOutcome,
    _receipt_path,
    canonical_receipt_bytes,
    collect_receipts,
    generate_receipt_id,
    list_receipt_ids,
    read_receipt_bytes,
    write_receipt,
)

__all__ = [
    "WriteOutcome",
    "_receipt_path",
    "canonical_receipt_bytes",
    "collect_receipts",
    "generate_receipt_id",
    "list_receipt_ids",
    "read_receipt_bytes",
    "write_receipt",
]
