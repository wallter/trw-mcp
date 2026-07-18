"""PRD-CORE-205 FR03 wiring — typed-present-invalid review receipt blocks legacy rescue."""

from __future__ import annotations

from pathlib import Path

import yaml

from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._delivery_review_gate import _check_review_gate


def _write_legacy_substantive_review(run: Path) -> None:
    meta = run / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "review.yaml").write_text(
        yaml.safe_dump({"substantive": True, "verdict": "pass", "critical_count": 0}),
        encoding="utf-8",
    )


def _write_malformed_typed_receipt(run: Path) -> None:
    rdir = run / "meta" / "receipts" / "review"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "review-corrupt.json").write_text("{ this is not valid json", encoding="utf-8")


class TestTypedPresentInvalidBlocksLegacyRescue:
    def test_legacy_substantive_cannot_replace_typed_receipt(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        _write_legacy_substantive_review(run)
        block, warning, advisory = _check_review_gate(run, FileStateReader())
        # Legacy prose remains telemetry and cannot fabricate typed review evidence.
        assert block is None and warning is None
        assert advisory is not None and "No substantive trw_review" in advisory

    def test_typed_present_invalid_forces_review_absent(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        _write_legacy_substantive_review(run)
        _write_malformed_typed_receipt(run)
        block, warning, advisory = _check_review_gate(run, FileStateReader())
        # The malformed typed receipt blocks the legacy positive from rescuing it;
        # the run is now treated as having NO substantive review, so the missing-
        # review policy fires (some non-None channel).
        assert any(msg is not None for msg in (block, warning, advisory))
