"""PRD-CORE-267 FR04 — the recall verification pass is wall-clock budgeted.

A live capture on 2026-09-05 measured a recall at 14,518 ms with 12,900 ms of
it inside this pass, having checked four entries with no cache hits — nearly
all of it spent re-verifying anchors the pre-FR01 derivation had fabricated.
FR04 bounds the pass and makes what it did not reach SAY SO, rather than
letting an unexamined entry carry a previous pass's verdict forward as though
it were this pass's finding.
"""

from __future__ import annotations

import time

import pytest

from tests._structlog_capture import captured_structlog  # noqa: F401
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._recall_assertion_verification import BUDGET_DEFERRED_STATUS, _verify_assertions


class _RecordingBackend:
    """A backend double that records every write the pass attempts."""

    def __init__(self) -> None:
        self.updates: list[str] = []

    def update(self, entry_id: str, **_fields: object) -> None:
        self.updates.append(entry_id)
        return None


def _anchored(entry_id: str) -> dict[str, object]:
    """A ranked-learning payload carrying one anchor and no assertions."""
    return {
        "id": entry_id,
        "namespace": "default",
        "summary": f"summary {entry_id}",
        "anchors": [
            {
                "file": "trw-mcp/src/trw_mcp/state/anchor_generation.py",
                "symbol_name": "generate_anchors",
                "symbol_type": "function",
                "signature": "def generate_anchors(...):",
                "line_range": [1, 1],
            }
        ],
    }


def _rank_unchanged(learnings: list[dict[str, object]], *_args: object, **_kwargs: object) -> list[dict[str, object]]:
    return learnings


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> _RecordingBackend:
    """Route the pass's backend resolution at a recording double."""
    recorder = _RecordingBackend()
    monkeypatch.setattr(
        "trw_mcp.tools._recall_assertion_verification._resolve_backend",
        lambda: recorder,
    )
    return recorder


def _exhaust_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the monotonic clock jump an hour after the deadline is computed.

    The pass reads the clock once to build the deadline and once per entry, so
    a second reading an hour later means the budget is spent before the first
    entry is examined — whatever the configured value is.
    """
    origin = time.monotonic()
    calls = {"n": 0}

    def stepped() -> float:
        calls["n"] += 1
        return origin if calls["n"] == 1 else origin + 3600.0

    monkeypatch.setattr(time, "monotonic", stepped)


def _run(config: TRWConfig, learnings: list[dict[str, object]]) -> list[dict[str, object]]:
    return _verify_assertions(learnings, ["anchor"], config, _rank_unchanged)


class TestVerificationBudget:
    def test_budget_exhaustion_marks_and_defers(
        self, backend: _RecordingBackend, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR04: past the budget, remaining entries are labelled and left unwritten."""
        config = TRWConfig(recall_verification_budget_ms=1000)
        learnings = [_anchored(f"L-{index:03d}") for index in range(4)]
        _exhaust_clock(monkeypatch)

        result = _run(config, learnings)

        assert [entry["verification_status"] for entry in result] == [BUDGET_DEFERRED_STATUS] * 4
        assert backend.updates == [], "a deferred entry must not be written"

    def test_ample_budget_verifies_every_entry(self, backend: _RecordingBackend) -> None:
        """With headroom nothing is deferred and the pass behaves as it did before."""
        config = TRWConfig(recall_verification_budget_ms=600_000)
        learnings = [_anchored(f"L-{index:03d}") for index in range(3)]

        result = _run(config, learnings)

        for entry in result:
            assert entry.get("verification_status") != BUDGET_DEFERRED_STATUS
        assert len(backend.updates) == 3

    def test_zero_budget_disables_the_bound(self, backend: _RecordingBackend, monkeypatch: pytest.MonkeyPatch) -> None:
        """0 is the documented "no bound" value, not "defer everything".

        The clock is deliberately exhausted here: with the bound disabled the
        pass must not consult it at all.
        """
        config = TRWConfig(recall_verification_budget_ms=0)
        _exhaust_clock(monkeypatch)

        result = _run(config, [_anchored("L-000")])

        assert result[0].get("verification_status") != BUDGET_DEFERRED_STATUS
        assert backend.updates == ["L-000"]

    def test_counters_record_carries_the_deferred_count(
        self,
        backend: _RecordingBackend,
        monkeypatch: pytest.MonkeyPatch,
        captured_structlog: list[dict[str, object]],
    ) -> None:
        """FR04: the single per-pass structured record reports the deferral."""
        config = TRWConfig(recall_verification_budget_ms=1000)
        learnings = [_anchored(f"L-{index:03d}") for index in range(2)]
        _exhaust_clock(monkeypatch)

        _run(config, learnings)

        counters = [record for record in captured_structlog if record.get("event") == "verification_pass_counters"]
        assert counters, "the pass must emit exactly one counters record"
        assert counters[0]["deferred"] == 2
        assert counters[0]["checked"] == 0

    def test_not_checked_budget_is_never_persisted(
        self, backend: _RecordingBackend, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NFR03: the marker is response-only; the persisted domain is unchanged.

        ``VerificationStatus`` is ``verified | stale | None``. If the deferral
        marker ever reached ``backend.update`` it would widen a persisted
        enumeration through the side door.
        """
        from trw_mcp.tools._verification_pass import VerificationStatus

        assert BUDGET_DEFERRED_STATUS not in str(VerificationStatus)

        config = TRWConfig(recall_verification_budget_ms=1000)
        _exhaust_clock(monkeypatch)

        _run(config, [_anchored("L-000")])

        assert backend.updates == []


class TestBudgetAndThresholdFields:
    def test_budget_and_threshold_fields_are_typed_and_admitted(self) -> None:
        """NFR02: both tunables are bounded, self-describing, and registered."""
        from trw_mcp.models.config._field_admission_registry import FIELD_ADMISSIONS

        fields = TRWConfig.model_fields
        for name, default in (
            ("recall_verification_budget_ms", 1000),
            ("anchor_shared_set_migration_threshold", 10),
        ):
            assert name in fields, name
            field = fields[name]
            assert field.default == default
            assert field.description, f"{name} must document itself"
            bounds = [meta for meta in field.metadata if hasattr(meta, "ge") or hasattr(meta, "le")]
            assert bounds, f"{name} must be bounded"
            admission = FIELD_ADMISSIONS.get(name)
            assert admission is not None, f"{name} must carry a ConfigAdmission record"
            assert admission.consumer
            assert admission.default_rationale
            assert admission.deprecation_plan

    def test_budget_default_is_reachable_through_get_config(self) -> None:
        """The field is live on the resolved config the pass actually reads."""
        from trw_mcp.models.config import get_config

        assert get_config().recall_verification_budget_ms == 1000
