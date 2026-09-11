"""Behavioral guards for FR04 at the delivery gate — the two properties review caught.

`test_contradiction_penalty_wiring.py` proves the CALL EXISTS (by AST). That is the
regression it was written for, and it is not enough on its own: an adversarial
pre-release review found two defects the wiring tests pass straight through.

**1. The gate must not be able to raise.** `apply_contradiction_penalty` reaches
`_default_lookup_entry` -> `backend.get()`, which is unguarded, so a
`sqlite3.OperationalError` from a locked or damaged store escaped into
`check_delivery_gates`. `_ceremony_deliver_tool` calls that gate with no try of its
own, and does so AFTER opening the PRD-CORE-208 delivery journal — so a raise both
failed `trw_deliver` and stranded the journal in a non-terminal state. The marker on
`unsettled_contradiction_ids` had already asserted the contract for both halves
("neither an advisory nor a reward signal may raise inside the delivery gate"); only
the advisory half was implemented.

**2. A stale contradiction must not penalise forever.** The cooldown is one UTC day,
but a stored failing assertion never expires, so an entry was re-penalised every day
until somebody retracted it. Q clamps to [0, 1], so it converges to 0, utility drops
below the delete threshold, and `learning_auto_prune_on_deliver` (default True)
nominates the entry obsolete. The more often a memory was recalled the faster it
decayed — inverting the signal's purpose — and FR06's own advisory tells the reader
NOT to retract until they re-verify, so following the advice sustained the decay.
FR04 is now bounded to evidence fresh within `verification_cache_ttl_seconds`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from trw_mcp.tools import _retraction_nudge

pytestmark = pytest.mark.unit


def _entry_with_failure(*, age_seconds: float) -> dict[str, object]:
    """A learning carrying one failing assertion observed ``age_seconds`` ago."""
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    return {"assertions": [{"last_result": False, "last_verified_at": stamp, "last_evidence": "probe failed"}]}


class TestFreshnessBound:
    """FR04 penalises only fresh contradictions; FR06 still names any age."""

    def test_a_fresh_failure_is_penalisable(self) -> None:
        assert _retraction_nudge._has_unsettled_contradiction(_entry_with_failure(age_seconds=60), ttl_seconds=3600)

    def test_a_stale_failure_is_not_penalisable(self) -> None:
        """The defect: without this bound the entry is re-penalised every UTC day,
        forever, on evidence the code itself stamps ``current_tree_verified: False``."""
        assert not _retraction_nudge._has_unsettled_contradiction(
            _entry_with_failure(age_seconds=86_400), ttl_seconds=3600
        )

    def test_the_advisory_still_names_a_stale_failure(self) -> None:
        """Non-vacuity partner, and the reason the two callers are separate: an
        unsettled contradiction does not stop mattering to a HUMAN because it aged.
        If a fix made the advisory freshness-bound too, FR06 would go quiet on
        exactly the old unretracted claims it exists to surface."""
        assert _retraction_nudge._has_unsettled_contradiction(_entry_with_failure(age_seconds=86_400), ttl_seconds=0)

    def test_an_entry_with_no_failing_assertion_is_never_penalisable(self) -> None:
        ok = {"assertions": [{"last_result": True, "last_verified_at": datetime.now(timezone.utc).isoformat()}]}
        assert not _retraction_nudge._has_unsettled_contradiction(ok, ttl_seconds=3600)

    def test_a_superseded_entry_is_never_penalisable(self) -> None:
        entry = _entry_with_failure(age_seconds=60)
        entry["invalidated_by"] = "L-newer"
        assert not _retraction_nudge._has_unsettled_contradiction(entry, ttl_seconds=3600)


class TestGateNeverRaises:
    """The delivery gate absorbs a failing reward signal.

    Both tests need a REAL run_path: `check_delivery_gates` returns early when it is
    None (`_delivery_helpers.py:369`), long before the FR04 block. The non-vacuity
    partner below caught exactly that while this file was being written — the first
    version passed with `run_path=None` and exercised nothing.
    """

    @staticmethod
    def _patch(monkeypatch: pytest.MonkeyPatch, penalty: Any) -> None:
        import trw_mcp.scoring as scoring

        monkeypatch.setattr(_retraction_nudge, "fresh_contradiction_ids", lambda _trw: ["L-contradicted"], raising=True)
        monkeypatch.setattr(scoring, "apply_contradiction_penalty", penalty, raising=True)

    def test_a_raising_penalty_does_not_escape_the_gate(
        self, monkeypatch: pytest.MonkeyPatch, sample_run_dir: Any, tmp_project: Any
    ) -> None:
        """Reproduces the traced path: the store raises while the penalty reads it.

        Asserts the gate still RETURNS a dict. Before the fix an OSError from
        `backend.get()` propagated out of `check_delivery_gates`, out of
        `trw_deliver`, and left the PRD-CORE-208 delivery journal non-terminal.
        """
        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools._delivery_helpers import check_delivery_gates

        def _boom(*_args: object, **_kwargs: object) -> list[str]:
            raise OSError("database is locked")

        self._patch(monkeypatch, _boom)
        result = check_delivery_gates(sample_run_dir, FileStateReader(), tmp_project / ".trw")
        assert isinstance(result, dict)

    def test_the_probe_actually_reaches_the_penalty(
        self, monkeypatch: pytest.MonkeyPatch, sample_run_dir: Any, tmp_project: Any
    ) -> None:
        """Non-vacuity partner. Without it, a fixture that returns early makes the
        test above pass while asserting nothing about the guard."""
        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools._delivery_helpers import check_delivery_gates

        called: list[list[str]] = []

        def _record(ids: list[str], _trw: Any) -> list[str]:
            called.append(list(ids))
            return []

        self._patch(monkeypatch, _record)
        check_delivery_gates(sample_run_dir, FileStateReader(), tmp_project / ".trw")
        assert called == [["L-contradicted"]], "the gate did not reach apply_contradiction_penalty"
