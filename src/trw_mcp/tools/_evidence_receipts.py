"""Evidence receipt service — PRD-CORE-205 (facade).

Single import point for the receipt substrate's behavior. Implementations live
in focused siblings under the 350 effective-LOC gate:

- ``_evidence_binding.py`` — stable reads, scope minting, content-binding
  construction + freshness revalidation (FR01/FR05).
- ``_evidence_persistence.py`` — atomic/idempotent writes, IDs, collision,
  GC, tombstones (FR09).
- ``_evidence_gates.py`` — shared review/build/verification validators and the
  observe/enforce mode reader (FR02-FR08).

Consumers (review gate, build gate, delivery, status) import from HERE.
"""

from __future__ import annotations

from trw_mcp.tools._evidence_binding import (
    BindingOutcome,
    StableReadError,
    build_content_binding,
    content_binding_is_current,
    mint_run_owned_scope,
    read_content_entry,
)
from trw_mcp.tools._evidence_gates import (
    read_evidence_mode,
    select_typed_review_state,
    validate_build_receipt,
    validate_review_receipt,
    validate_verification_receipt,
)
from trw_mcp.tools._evidence_persistence import (
    WriteOutcome,
    canonical_receipt_bytes,
    collect_receipts,
    generate_receipt_id,
    list_receipt_ids,
    read_receipt_bytes,
    write_receipt,
)

__all__ = [
    "BindingOutcome",
    "StableReadError",
    "WriteOutcome",
    "build_content_binding",
    "canonical_receipt_bytes",
    "collect_receipts",
    "content_binding_is_current",
    "generate_receipt_id",
    "list_receipt_ids",
    "mint_run_owned_scope",
    "read_content_entry",
    "read_evidence_mode",
    "read_receipt_bytes",
    "select_typed_review_state",
    "validate_build_receipt",
    "validate_review_receipt",
    "validate_verification_receipt",
    "write_receipt",
]
