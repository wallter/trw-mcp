"""Evidence section of the evidence pack (PRD-CORE-323 FR03, NFR02 receipt cap).

Each persisted receipt yields two entries. ``record`` (``as_of: run_record``) copies
what the run persisted: the receipt id, a file digest (raw bytes only when
redaction left the receipt unchanged), git_sha, the content-binding scope digest,
and per type the reporter origin and integration claims (build), verdict and
degraded_reason (review), or requirement and outcome (verification).
``at_export`` (``as_of: export_snapshot``) reports ``positivity_at_export`` from
the trust gate's own per-receipt classifier, recomputed against the export tree.

Soundness scope: proves each receipt's bytes as persisted, and whether the trust
gate would treat the receipt as positive against the export-time tree. It does not
prove the commands behind a receipt ran as reported, or that the receipt was
positive when ``trw_deliver`` read it. Capability-integration rows are never
recomputed, so the export never registers tools on a probe server.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.evidence_pack._redaction import EntryWriter, JsonValue
from trw_mcp.evidence_pack._wording import AS_OF_EXPORT_SNAPSHOT, AS_OF_RUN_RECORD

RECEIPT_TYPES: tuple[str, ...] = ("build", "review", "verification")

#: NFR02: receipts kept per type; the rest are dropped with kept and total counts recorded.
MAX_RECEIPTS_PER_TYPE = 1000


def _record_fields(receipt_type: str, receipt: object) -> dict[str, object]:
    """Copy the FR03 fields of a parsed receipt; no field is derived here."""
    from trw_mcp.models._evidence_records import BuildReceipt, ReviewReceipt, VerificationReceipt
    from trw_mcp.tools._command_results import integration_claims

    if isinstance(receipt, BuildReceipt):
        return {
            "git_sha": receipt.git_sha,
            "scope_digest": receipt.content_binding.scope_digest,
            "reporter_origin": receipt.reporter_origin,
            "integration_claims": integration_claims(receipt.command_results),
        }
    if isinstance(receipt, ReviewReceipt):
        return {
            "git_sha": None,
            "scope_digest": receipt.content_binding.scope_digest,
            "verdict": receipt.verdict.value,
            "degraded_reason": receipt.degraded_reason,
        }
    if isinstance(receipt, VerificationReceipt):
        return {
            "git_sha": None,
            "scope_digest": receipt.content_binding.scope_digest,
            "requirement_id": receipt.requirement_id,
            "outcome": receipt.outcome.value,
        }
    raise TypeError(f"not a {receipt_type} receipt: {type(receipt).__name__}")


def _parse(run_path: Path, receipt_type: str, raw: bytes, project_root: Path) -> tuple[object | None, bool, str]:
    """Return (parsed receipt or None, positivity_at_export, reason) via the shared classifier."""
    from trw_mcp.models._evidence_records import ReviewReceipt
    from trw_mcp.state._trust_receipts import classify_receipt

    verdict = classify_receipt(run_path, receipt_type, raw, project_root)
    if receipt_type != "review":
        return verdict.receipt, verdict.positive, verdict.reason
    try:
        return ReviewReceipt.model_validate_json(raw), verdict.positive, verdict.reason
    except Exception:  # trw-fail-silent-allow: a malformed receipt is listed unknown/receipt_unparsable, never dropped
        return None, False, "receipt_unparsable"


def _receipt_item(
    run_path: Path,
    run_source: str,
    receipt_type: str,
    receipt_id: str,
    context: tuple[Path, EntryWriter, dict[str, JsonValue]],
) -> dict[str, JsonValue]:
    from trw_mcp.state._evidence_persistence import read_receipt_bytes

    project_root, writer, snapshot = context
    source: dict[str, JsonValue] = {"path": f"{run_source}/meta/receipts/{receipt_type}/{receipt_id}.json"}
    raw = read_receipt_bytes(run_path, receipt_type, receipt_id)
    if raw is None:
        record = writer.seal(
            source=source, as_of=AS_OF_RUN_RECORD, fields={"receipt_id": receipt_id}, reason="receipt_unreadable"
        )
        return {"receipt_id": record["receipt_id"], "record": record}
    receipt, positive, reason = _parse(run_path, receipt_type, raw, project_root)
    fields: dict[str, object] = {"receipt_id": receipt_id, **writer.file_digest(raw)}
    if receipt is None:
        record = writer.seal(source=source, as_of=AS_OF_RUN_RECORD, fields=fields, reason="receipt_unparsable")
    else:
        record = writer.seal(
            source=source, as_of=AS_OF_RUN_RECORD, fields={**fields, **_record_fields(receipt_type, receipt)}
        )
    at_export = writer.seal(
        source=source,
        as_of=AS_OF_EXPORT_SNAPSHOT,
        fields={"positivity_at_export": positive, "positivity_reason": reason, **snapshot},
    )
    # The outer id is the record's redacted, bounded copy, never the raw file stem.
    return {"receipt_id": record["receipt_id"], "record": record, "at_export": at_export}


def evidence_section(
    run_path: Path,
    run_source: str,
    project_root: Path,
    export_head: str | None,
    writer: EntryWriter,
) -> dict[str, JsonValue]:
    """List every persisted receipt per type, capped, with positivity recomputed at export.

    Runtime caller: ``_pack.build_pack`` (``trw-mcp run evidence-pack`` -> ``run_run``
    -> ``build_pack`` -> here -> ``state._trust_receipts.classify_receipt``).
    ``export_head`` is ``tools._evidence_git.clean_git_sha`` taken once per export;
    ``None`` means the tree is dirty or has no Git answer. The cap is applied to the
    sorted id listing before any receipt is read.
    """
    from trw_mcp.state._evidence_persistence import list_receipt_ids

    snapshot: dict[str, JsonValue] = {
        "derivation": "recomputed_at_export",
        "export_head": export_head,
        "tree_state": "clean" if export_head else "dirty_or_unknown",
    }
    receipts: dict[str, JsonValue] = {}
    for receipt_type in RECEIPT_TYPES:
        ids = list_receipt_ids(run_path, receipt_type)
        kept = ids[:MAX_RECEIPTS_PER_TYPE]
        items: list[JsonValue] = [
            _receipt_item(run_path, run_source, receipt_type, rid, (project_root, writer, snapshot)) for rid in kept
        ]
        receipts[receipt_type] = {
            "found": f"{len(ids)} {receipt_type} receipts found",
            "total": len(ids),
            "kept": len(kept),
            "items": items,
        }
    capability = writer.seal(
        source={"path": f"{run_source}/meta"},
        as_of=AS_OF_RUN_RECORD,
        fields={"row": "capability_integration"},
        reason="not_persisted_by_deliver",
    )
    return {"section": "evidence", "receipts": receipts, "capability_integration": capability}
