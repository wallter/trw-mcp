"""Content-bound evidence receipt models — PRD-CORE-205 (facade).

Public import point for the evidence-receipt data contracts. Implementations
live in focused siblings to stay under the 350 effective-LOC gate:

- ``_evidence_core.py`` — limits, enums, ContentEntry/ContentBinding,
  RunOwnedScope, ReceiptValidationResult, canonicalization primitives.
- ``_evidence_plans.py`` — RequiredReviewPlan, RequiredValidationPlan,
  BuildCommandResult, verdict/outcome enums.
- ``_evidence_records.py`` — ReviewReceipt, BuildReceipt, VerificationReceipt,
  ReceiptTombstone.

Downstream consumers (PRD-CORE-206/208) SHOULD import receipt types from THIS
module so the split remains an internal refactor detail.
"""

from __future__ import annotations

from trw_mcp.models._evidence_core import (
    CANONICAL_ALGORITHM,
    SCHEMA_VERSION,
    ContentBinding,
    ContentEntry,
    EntryState,
    EvidenceLimits,
    EvidenceMode,
    ReceiptState,
    ReceiptValidationResult,
    RunOwnedScope,
    ScopeConfidence,
    canonical_json,
    compute_manifest_digest,
    compute_scope_digest,
    domain_digest,
)
from trw_mcp.models._evidence_plans import (
    BuildCommandResult,
    CommandClass,
    ExecutionProvenance,
    RequiredReviewPlan,
    RequiredValidationPlan,
    ReviewVerdict,
    VerificationOutcome,
)
from trw_mcp.models._evidence_records import (
    BuildReceipt,
    ReceiptTombstone,
    ReviewReceipt,
    VerificationReceipt,
)

__all__ = [
    "CANONICAL_ALGORITHM",
    "SCHEMA_VERSION",
    "BuildCommandResult",
    "BuildReceipt",
    "CommandClass",
    "ContentBinding",
    "ContentEntry",
    "EntryState",
    "EvidenceLimits",
    "EvidenceMode",
    "ExecutionProvenance",
    "ReceiptState",
    "ReceiptTombstone",
    "ReceiptValidationResult",
    "RequiredReviewPlan",
    "RequiredValidationPlan",
    "ReviewReceipt",
    "ReviewVerdict",
    "RunOwnedScope",
    "ScopeConfidence",
    "VerificationOutcome",
    "VerificationReceipt",
    "canonical_json",
    "compute_manifest_digest",
    "compute_scope_digest",
    "domain_digest",
]
