"""Governance evidence pack (PRD-CORE-323).

Evidence pack for readiness review. Not a conformance, certification or
compliance claim.

Responsibility: turn one run directory into one deterministic, bounded, redacted
canonical-JSON pack that lists the run's requirements, decisions, evidence and
verdict, each entry with a source path, a label (observed, redacted, unknown), an
``as_of`` (run_record or export_snapshot) and a sha256 over its own redacted bytes.

Interface: :func:`build_pack` (``trw-mcp run evidence-pack``) and
:class:`PackRefusedError`. ``verify_pack`` arrives with slice S3.

Invariants:
- every copied string passes ``redact_secrets`` then ``redact_paths``, then is
  truncated to 4,096 characters, then digested; no digest of unredacted text is
  ever published (``_redaction``);
- the same run and tree give byte-identical packs: no wall-clock value is read;
- the export reads only; it never opens the memory store or starts a probe
  server, opens the delivery journal read-only without ever creating it, and
  emits at most 2,000 checkpoints, 5,000 decision-class events, 1,000 receipts
  per type, 500 override records, 1,000 journal operations and 8 MiB total.
"""

from __future__ import annotations

from trw_mcp.evidence_pack._manifest import PackRefusedError
from trw_mcp.evidence_pack._pack import build_pack

__all__ = ["PackRefusedError", "build_pack"]
