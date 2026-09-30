"""E2E-COV-GATE-MODE-REVIEW: deliver-gate branches no test executed (coverage audit, trunk 87368e804b).

Gate mode: an uncomputable change count and an unintrospectable config default must fail CLOSED.
Review gate: a non-boolean ``substantive`` is not a review; malformed legacy evidence is absent (the
complexity policy still applies); and a ``block`` verdict with critical findings on a run whose complexity
cannot be read is downgraded to a WARNING that NAMES the downgrade -- lenient by design, never silent.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from trw_mcp.models._evidence_core import EvidenceMode
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools import _delivery_helpers as dh
from trw_mcp.tools._deliver_gate_mode import count_session_changed_files, resolve_gate_mode_with_source
from trw_mcp.tools._delivery_review_gate import evaluate_review_gate

_DOWNGRADE = "complexity could not be established"


def _run(tmp_path: Path) -> Path:
    run = tmp_path / ".trw" / "runs" / "task" / "run-1"
    (run / "meta").mkdir(parents=True)
    return run


class TestChangeCountFailsClosed:
    def test_an_unreadable_edit_since_boot_makes_the_count_uncomputable(self, tmp_path: Path) -> None:
        run = _run(tmp_path)
        stream = tmp_path / ".trw" / "context" / "session-events.jsonl"
        stream.parent.mkdir(parents=True)
        assert count_session_changed_files(events=[], run_path=run, session_id="s") == 0  # positive control

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        stream.write_text(json.dumps({"event": "change_evidence_unknown", "ts": now}) + "\n", encoding="utf-8")

        assert count_session_changed_files(events=[], run_path=run, session_id="s") is None

    def test_a_counter_that_raises_is_uncomputable_not_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*_a: object, **_k: object) -> int:
            raise RuntimeError("counter bug")

        monkeypatch.setattr("trw_mcp.tools._delivery_event_checks._count_file_modified_current_session", _boom)

        assert count_session_changed_files(events=[], run_path=_run(tmp_path), session_id="s") is None


class TestUnreadableModeDefault:
    def test_an_unintrospectable_default_falls_back_to_block_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise() -> object:
            raise RuntimeError("config unreadable")

        monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", _raise)
        monkeypatch.setattr("trw_mcp.models.config.TRWConfig", SimpleNamespace())  # no model_fields to read

        assert resolve_gate_mode_with_source("coding") == ("block_all", True)


@pytest.fixture
def legacy_observe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Typed evidence absent + OBSERVE mode: the legacy ``review.yaml`` projection is consulted."""
    monkeypatch.setattr(
        "trw_mcp.tools._review_receipt_writer.load_latest_review_evidence",
        lambda *_a: (SimpleNamespace(typed_present=False), None),
    )
    monkeypatch.setattr("trw_mcp.tools._evidence_gates.read_evidence_mode", lambda _c: EvidenceMode.OBSERVE)
    monkeypatch.setattr("trw_mcp.tools._delivery_review_gate._review_gate_mode_is_block", lambda _c: True)


def _complexity(monkeypatch: pytest.MonkeyPatch, value: str, *, readable: bool = True) -> None:
    monkeypatch.setattr(dh, "_read_complexity_class", lambda *_a: value)
    monkeypatch.setattr(dh, "run_yaml_is_readable", lambda *_a: readable)


def _review_yaml(run: Path, body: str) -> None:
    (run / "meta" / "review.yaml").write_text(body, encoding="utf-8")


@pytest.mark.usefixtures("legacy_observe")
class TestLegacyReviewEvidence:
    @pytest.mark.parametrize("flag", ['"true"', "1", "yes-please"], ids=["str-true", "int-one", "text"])
    def test_a_non_boolean_substantive_flag_is_not_a_review(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str
    ) -> None:
        run = _run(tmp_path)
        _complexity(monkeypatch, "STANDARD")
        _review_yaml(run, "substantive: true\nverdict: pass\n")
        assert evaluate_review_gate(run, FileStateReader()).block is None  # positive control

        _review_yaml(run, f"substantive: {flag}\nverdict: pass\n")
        outcome = evaluate_review_gate(run, FileStateReader())

        assert outcome.block is not None and "No substantive trw_review was recorded" in outcome.block

    def test_malformed_legacy_evidence_is_absent_and_the_complexity_policy_still_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = _run(tmp_path)
        _complexity(monkeypatch, "STANDARD")
        _review_yaml(run, "substantive: true\nverdict: block\ncritical_count: lots\n")

        outcome = evaluate_review_gate(run, FileStateReader())

        assert outcome.block is not None and "No substantive trw_review was recorded" in outcome.block

    def test_a_block_verdict_on_an_unclassifiable_run_warns_and_names_the_downgrade(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = _run(tmp_path)
        _review_yaml(run, "substantive: true\nverdict: block\ncritical_count: 2\n")
        _complexity(monkeypatch, "STANDARD")
        assert evaluate_review_gate(run, FileStateReader()).block is not None  # positive control

        _complexity(monkeypatch, "", readable=False)
        outcome = evaluate_review_gate(run, FileStateReader())

        assert outcome.block is None
        assert outcome.warning is not None
        assert "'block' with 2 critical finding(s)" in outcome.warning and _DOWNGRADE in outcome.warning


class TestTypedReviewEvidence:
    def test_a_typed_block_verdict_on_an_unclassifiable_run_warns_and_names_the_downgrade(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        receipt = SimpleNamespace(
            receipt_id="review-x",
            verdict=SimpleNamespace(value="block"),
            findings=(SimpleNamespace(severity="critical"), SimpleNamespace(severity="minor")),
            content_binding=SimpleNamespace(scope_digest="d"),
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        state = SimpleNamespace(typed_present=True, is_positive=True, reason_code="", receipt_id="review-x")
        monkeypatch.setattr(
            "trw_mcp.tools._review_receipt_writer.load_latest_review_evidence", lambda *_a: (state, receipt)
        )
        run = _run(tmp_path)
        _complexity(monkeypatch, "STANDARD")
        assert evaluate_review_gate(run, FileStateReader()).block is not None  # positive control

        _complexity(monkeypatch, "", readable=False)
        outcome = evaluate_review_gate(run, FileStateReader())

        assert outcome.block is None
        assert outcome.warning is not None
        assert "'block' with 1 critical finding(s)" in outcome.warning and _DOWNGRADE in outcome.warning
