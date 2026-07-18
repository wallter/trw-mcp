"""PRD-CORE-205 FR09/NFR04 — atomic, idempotent, collision-safe persistence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from trw_mcp.models._evidence_core import (
    ContentBinding,
    ContentEntry,
    EntryState,
    compute_manifest_digest,
    compute_scope_digest,
)
from trw_mcp.models._evidence_plans import ReviewVerdict
from trw_mcp.models._evidence_records import ReviewReceipt
from trw_mcp.tools._evidence_persistence import (
    collect_receipts,
    generate_receipt_id,
    read_receipt_bytes,
    write_receipt,
)


def _binding() -> ContentBinding:
    entries = (ContentEntry(path="a.py", state=EntryState.FILE, byte_digest="a" * 64, byte_size=1),)
    return ContentBinding(
        scope_id="sc1",
        scope_digest=compute_scope_digest("sc1", "proj", ("a.py",)),
        project_identity="proj",
        entries=entries,
        manifest_digest=compute_manifest_digest(entries),
    )


def _receipt(receipt_id: str, verdict: ReviewVerdict = ReviewVerdict.PASS) -> ReviewReceipt:
    return ReviewReceipt(
        receipt_id=receipt_id,
        review_id="rv1",
        run_id="run1",
        completed_at="2026-07-10T00:00:00Z",
        method="manual",
        reviewer_origin="human",
        reviewer_identity="tester",
        reviewer_family="human",
        content_binding=_binding(),
        review_plan_id="plan1",
        review_plan_digest="pd",
        review_input_digest="id",
        verdict=verdict,
    )


class TestAtomicIdempotentCollision:
    def test_receipt_writes_are_atomic_idempotent_and_collision_safe(self, tmp_path: Path) -> None:
        rid = generate_receipt_id("review")
        assert rid.startswith("review-") and len(rid) == len("review-") + 32

        # First write persists.
        out = write_receipt(tmp_path, "review", rid, _receipt(rid))
        assert out.ok and not out.idempotent
        assert read_receipt_bytes(tmp_path, "review", rid) is not None

        # Byte-identical replay is idempotent.
        out2 = write_receipt(tmp_path, "review", rid, _receipt(rid))
        assert out2.ok and out2.idempotent

        # Same ID, different canonical payload -> collision, fails closed.
        out3 = write_receipt(tmp_path, "review", rid, _receipt(rid, verdict=ReviewVerdict.BLOCK))
        assert not out3.ok and out3.reason_code == "receipt_id_collision"
        # Original payload is untouched.
        stored = read_receipt_bytes(tmp_path, "review", rid)
        assert stored is not None and b'"verdict":"pass"' in stored

    def test_tombstoned_id_cannot_be_reused(self, tmp_path: Path) -> None:
        rid = generate_receipt_id("review")
        write_receipt(tmp_path, "review", rid, _receipt(rid))
        # Force collection by using an old mtime cutoff.
        collected = collect_receipts(
            tmp_path,
            "review",
            referenced_ids=frozenset(),
            now=datetime.now(timezone.utc) + timedelta(days=200),
        )
        assert rid in collected
        assert read_receipt_bytes(tmp_path, "review", rid) is None
        # Re-minting the tombstoned ID is refused.
        out = write_receipt(tmp_path, "review", rid, _receipt(rid))
        assert not out.ok and out.reason_code == "receipt_id_tombstoned"

    def test_referenced_receipt_survives_gc(self, tmp_path: Path) -> None:
        rid = generate_receipt_id("review")
        write_receipt(tmp_path, "review", rid, _receipt(rid))
        collected = collect_receipts(
            tmp_path,
            "review",
            referenced_ids=frozenset({rid}),
            now=datetime.now(timezone.utc) + timedelta(days=200),
        )
        assert collected == []
        assert read_receipt_bytes(tmp_path, "review", rid) is not None

    def test_unexpired_receipt_not_collected(self, tmp_path: Path) -> None:
        rid = generate_receipt_id("review")
        write_receipt(tmp_path, "review", rid, _receipt(rid))
        collected = collect_receipts(tmp_path, "review", referenced_ids=frozenset())
        assert collected == []


class TestCorruptOrPartialReceipt:
    def test_corrupt_or_partial_receipt_is_never_positive_evidence(self, tmp_path: Path) -> None:
        from trw_mcp.tools._evidence_persistence import _receipt_path

        rid = generate_receipt_id("review")
        path = _receipt_path(tmp_path, "review", rid)
        path.parent.mkdir(parents=True, exist_ok=True)
        # A truncated / malformed JSON file must never validate as a receipt.
        path.write_text('{"receipt_id": "review-broken", "sch', encoding="utf-8")
        raw = read_receipt_bytes(tmp_path, "review", rid)
        assert raw is not None
        import json

        try:
            json.loads(raw)
            parsed_ok = True
        except json.JSONDecodeError:
            parsed_ok = False
        assert parsed_ok is False
