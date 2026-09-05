"""PRD-CORE-255-FR01 — a review verdict expires by ELAPSED TIME.

Fails before the change: ``validate_review_receipt`` had no time axis at all, so
a receipt whose bound bytes never moved stayed ``VALID`` forever. Every test here
that asserts a non-``VALID`` state for an old-but-content-current receipt is red
on the pre-CORE-255 tree.

The TTL is deliberately INDEPENDENT of the PRD-CORE-205 content binding: both
axes must pass, and neither substitutes for the other.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.models._evidence_core import ReceiptState
from trw_mcp.models.config import TRWConfig
from trw_mcp.state._evidence_gates import (
    REVIEW_VERDICT_EXPIRED,
    review_receipt_age_seconds,
    review_verdict_is_expired,
    validate_review_receipt,
)
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._delivery_review_gate import _check_review_gate
from trw_mcp.tools._review_manual import handle_manual_mode

from ._evidence_factories import project_with_binding, review_plan, review_receipt


def _hours_ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _pin_ttl(monkeypatch: pytest.MonkeyPatch, hours: int) -> None:
    config = TRWConfig().model_copy(update={"review_verdict_ttl_hours": hours})
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)


class TestReceiptTtlValidation:
    def test_receipt_expires_after_ttl_window(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FR01 acceptance: 25h old under a 24h TTL is non-VALID with the dedicated code."""
        _pin_ttl(monkeypatch, 24)
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = review_plan(binding)
        receipt = review_receipt(binding, plan, completed_at=_hours_ago(25))

        result = validate_review_receipt(receipt, plan, project)

        assert not result.is_positive
        assert result.reason_code == REVIEW_VERDICT_EXPIRED
        assert result.receipt_id == receipt.receipt_id
        # Distinguishable from the content-binding failure in logs and tests.
        assert result.reason_code != "bound_content_changed"

    def test_receipt_inside_window_is_not_expired(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _pin_ttl(monkeypatch, 24)
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = review_plan(binding)
        receipt = review_receipt(binding, plan, completed_at=_hours_ago(1))

        assert validate_review_receipt(receipt, plan, project).state is ReceiptState.VALID

    def test_ttl_is_independent_of_the_content_binding(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A FRESH receipt over CHANGED bytes still fails, on the other axis."""
        _pin_ttl(monkeypatch, 24)
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = review_plan(binding)
        receipt = review_receipt(binding, plan, completed_at=_hours_ago(0.1))
        (project / "src" / "a.py").write_text("mutated", encoding="utf-8")

        result = validate_review_receipt(receipt, plan, project)

        assert not result.is_positive
        assert result.reason_code != REVIEW_VERDICT_EXPIRED

    def test_configured_window_is_honored_not_hardcoded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = review_plan(binding)
        receipt = review_receipt(binding, plan, completed_at=_hours_ago(48))

        _pin_ttl(monkeypatch, 24)
        assert validate_review_receipt(receipt, plan, project).reason_code == REVIEW_VERDICT_EXPIRED
        _pin_ttl(monkeypatch, 168)
        assert validate_review_receipt(receipt, plan, project).state is ReceiptState.VALID

    def test_unparseable_completed_at_is_expired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _pin_ttl(monkeypatch, 24)
        assert review_verdict_is_expired("not-a-timestamp") is True
        assert review_verdict_is_expired("") is True

    def test_naive_timestamp_is_read_as_utc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _pin_ttl(monkeypatch, 24)
        naive = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None).isoformat()
        assert review_verdict_is_expired(naive) is False

    def test_age_helper_reports_none_for_unreadable_stamp(self) -> None:
        assert review_receipt_age_seconds("garbage") is None
        assert (review_receipt_age_seconds(_hours_ago(2)) or 0.0) > 7000


def _project_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A real project + run whose journal names one modified file and one PRD."""
    project = tmp_path
    source = project / "src" / "feature.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    prd = project / "docs" / "requirements-aare-f" / "prds" / "PRD-CORE-255.md"
    prd.parent.mkdir(parents=True, exist_ok=True)
    prd.write_text("---\nprd:\n  id: PRD-CORE-255\n---\n\n# body\n", encoding="utf-8")
    run = project / ".trw" / "runs" / "task" / "run-1"
    meta = run / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "events.jsonl").write_text(
        json.dumps({"event": "file_modified", "file": str(source)}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project))
    return project, run


class TestExpiredVerdictIsSurfacedAtDeliverTime:
    def test_expired_message_names_receipt_and_remedy(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FR01: the deliver-time message names the EXPIRED receipt id + the remedy."""
        _project_run(tmp_path, monkeypatch)
        run = tmp_path / ".trw" / "runs" / "task" / "run-1"
        result = handle_manual_mode(
            [],
            run,
            "review-1",
            _hours_ago(72),
            ["PRD-CORE-255"],
            review_completed=True,
        )
        receipt_id = str(result["review_receipt_id"])
        assert receipt_id, "precondition: a typed receipt must exist for it to expire"

        config = TRWConfig().model_copy(
            update={
                "evidence_receipt_mode": "enforce",
                "review_gate_mode": "block",
                "review_verdict_ttl_hours": 24,
            }
        )
        monkeypatch.setattr("trw_mcp.tools._delivery_helpers.get_config", lambda: config)
        monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)
        monkeypatch.setattr("trw_mcp.tools._delivery_helpers._read_complexity_class", lambda *_: "STANDARD")

        block, _warning, _advisory = _check_review_gate(run, FileStateReader())

        assert block is not None
        assert receipt_id in block
        assert "EXPIRED" in block
        assert "re-run trw_review" in block
        assert "/mcp reconnect" in block

    def test_mid_run_expiry_has_no_separate_grace_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """US-001 AC3: a review that expired MID-RUN gets the same message as a cold expiry.

        Same run, same receipt; only the TTL moves. There is no grace path, so the
        surfaced text must be byte-identical to the cold-start case.
        """
        _project_run(tmp_path, monkeypatch)
        run = tmp_path / ".trw" / "runs" / "task" / "run-1"
        result = handle_manual_mode([], run, "review-1", _hours_ago(5), ["PRD-CORE-255"], review_completed=True)
        receipt_id = str(result["review_receipt_id"])
        monkeypatch.setattr("trw_mcp.tools._delivery_helpers._read_complexity_class", lambda *_: "STANDARD")

        def _pin(ttl: int) -> TRWConfig:
            cfg = TRWConfig().model_copy(
                update={
                    "evidence_receipt_mode": "enforce",
                    "review_gate_mode": "block",
                    "review_verdict_ttl_hours": ttl,
                }
            )
            monkeypatch.setattr("trw_mcp.tools._delivery_helpers.get_config", lambda: cfg)
            monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: cfg)
            return cfg

        _pin(24)
        fresh_block, _, _ = _check_review_gate(run, FileStateReader())
        assert fresh_block is None, "precondition: the receipt starts INSIDE the window"

        # Elapsed time crosses the window (modelled by tightening the window).
        _pin(1)
        expired_block, _, _ = _check_review_gate(run, FileStateReader())
        assert expired_block is not None
        assert receipt_id in expired_block
        assert "re-run trw_review" in expired_block
