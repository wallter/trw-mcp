"""Tests for assertion health summary in trw_session_start (PRD-CORE-086 FR07).

Verifies that the assertion_health computation logic correctly counts
passing, failing, stale, and unverifiable assertions from cached
last_result fields on entries with assertions.

The computation is tested in isolation (matching ceremony.py lines 369-400)
since the full trw_session_start has many orchestration dependencies.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock


def _make_mock_assertion(
    last_result: bool | None = None,
    last_verified_at: datetime | None = None,
) -> MagicMock:
    """Create a mock Assertion object with the given verification state."""
    a = MagicMock()
    a.last_result = last_result
    a.last_verified_at = last_verified_at
    return a


def _make_mock_entry(assertions: list[MagicMock]) -> MagicMock:
    """Create a mock MemoryEntry with the given assertions."""
    entry = MagicMock()
    entry.assertions = assertions
    return entry


def _compute_assertion_health(
    entries_with_assertions: list[MagicMock],
    stale_threshold_days: int = 7,
) -> dict[str, int] | None:
    """Reproduce the assertion_health computation from ceremony.py.

    This mirrors the logic at ceremony.py lines 369-400 so tests verify
    the exact algorithm used in production.

    Returns None when no entries have assertions (health is omitted).
    """
    if not entries_with_assertions:
        return None

    now = datetime.now(timezone.utc)
    stale_threshold = now - timedelta(days=stale_threshold_days)
    ah_passing = 0
    ah_failing = 0
    ah_stale = 0
    ah_unverifiable = 0

    for entry in entries_with_assertions:
        for a in entry.assertions:
            if a.last_verified_at is None or a.last_verified_at < stale_threshold:
                ah_stale += 1
            elif a.last_result is True:
                ah_passing += 1
            elif a.last_result is False:
                ah_failing += 1
            else:
                ah_unverifiable += 1

    return {
        "passing": ah_passing,
        "failing": ah_failing,
        "stale": ah_stale,
        "unverifiable": ah_unverifiable,
        "total": len(entries_with_assertions),
    }


class TestHealthSummaryPresentWhenAssertionsExist:
    """assertion_health is populated when backend has entries with assertions."""

    def test_health_summary_present_when_assertions_exist(self) -> None:
        """When entries have assertions, health dict contains all expected keys."""
        now = datetime.now(timezone.utc)
        recent = now - timedelta(hours=1)

        entries = [
            _make_mock_entry(
                [
                    _make_mock_assertion(last_result=True, last_verified_at=recent),
                    _make_mock_assertion(last_result=False, last_verified_at=recent),
                ]
            ),
        ]

        health = _compute_assertion_health(entries)

        assert health is not None
        assert health["passing"] == 1
        assert health["failing"] == 1
        assert health["stale"] == 0
        assert health["unverifiable"] == 0
        assert health["total"] == 1


class TestHealthSummaryOmittedWhenNoAssertions:
    """assertion_health is None (omitted) when no entries have assertions."""

    def test_health_summary_omitted_when_no_assertions(self) -> None:
        """Empty entries list produces None — health key is not added to results."""
        health = _compute_assertion_health([])
        assert health is None


class TestHealthCountsMatchStates:
    """Given entries with known states, counts accurately match expected values."""

    def test_health_counts_match_states(self) -> None:
        """Counts reflect passing, failing, stale, and unverifiable assertions."""
        now = datetime.now(timezone.utc)
        recent = now - timedelta(hours=1)
        old = now - timedelta(days=10)  # Older than 7-day stale threshold

        entries = [
            # Entry 1: one passing, one stale (never verified)
            _make_mock_entry(
                [
                    _make_mock_assertion(last_result=True, last_verified_at=recent),
                    _make_mock_assertion(last_result=None, last_verified_at=None),
                ]
            ),
            # Entry 2: one failing, one stale (verified long ago)
            _make_mock_entry(
                [
                    _make_mock_assertion(last_result=False, last_verified_at=recent),
                    _make_mock_assertion(last_result=True, last_verified_at=old),
                ]
            ),
            # Entry 3: one unverifiable (recently verified but result is None)
            _make_mock_entry(
                [
                    _make_mock_assertion(last_result=None, last_verified_at=recent),
                ]
            ),
        ]

        health = _compute_assertion_health(entries)

        assert health is not None
        assert health["passing"] == 1  # Entry 1, assertion 1
        assert health["failing"] == 1  # Entry 2, assertion 1
        assert health["stale"] == 2  # Entry 1 a2 (None) + Entry 2 a2 (old)
        assert health["unverifiable"] == 1  # Entry 3, assertion 1
        assert health["total"] == 3

        # Verify total assertions across all entries sum correctly
        total_assertions = health["passing"] + health["failing"] + health["stale"] + health["unverifiable"]
        assert total_assertions == 5


# ---------------------------------------------------------------------------
# PRD-CORE-263-FR08 — the step reads the configured stale threshold
# ---------------------------------------------------------------------------


def _entry_verified_days_ago(days: int) -> MagicMock:
    return _make_mock_entry(
        [_make_mock_assertion(last_result=True, last_verified_at=datetime.now(timezone.utc) - timedelta(days=days))]
    )


def _run_step(stale_days: int, entries: list[MagicMock]) -> dict[str, int] | None:
    from pathlib import Path
    from unittest.mock import patch

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools._ceremony_session_start_steps import step_assertion_health

    backend = MagicMock()
    backend.entries_with_assertions.return_value = entries
    cfg = TRWConfig(assertion_stale_threshold_days=stale_days)  # type: ignore[call-arg]
    with patch("trw_mcp.state.memory_adapter.get_backend", return_value=backend):
        return step_assertion_health(Path("/nonexistent"), None, cfg)


def test_assertion_health_uses_the_configured_stale_threshold() -> None:
    """PRD-CORE-263-FR08 — a 10-day-old assertion at the 30-day default is FRESH.

    At HEAD the step hardcoded ``timedelta(days=7)`` while the admitted config
    field defaults to 30, so session start and ``_verification_pass`` reported
    different stale counts from the same store. Attribution: reverting FR08
    turns the 30-day case red (the assertion is counted stale under the
    hardcoded 7-day window).
    """
    ten_days = [_entry_verified_days_ago(10)]

    at_default = _run_step(30, ten_days)
    assert at_default is not None
    assert at_default["stale"] == 0
    assert at_default["passing"] == 1

    at_seven = _run_step(7, ten_days)
    assert at_seven is not None
    assert at_seven["stale"] == 1
    assert at_seven["passing"] == 0


def test_assertion_health_agrees_with_the_maintenance_pass_on_the_same_store() -> None:
    """PRD-CORE-263-FR08 — the two surfaces read ONE threshold.

    ``_verification_pass`` already honoured ``assertion_stale_threshold_days``;
    this pins that both now classify the same corpus identically.
    """
    from datetime import datetime as _dt

    from trw_mcp.models.config import TRWConfig

    threshold_days = TRWConfig().assertion_stale_threshold_days
    entries = [_entry_verified_days_ago(d) for d in (1, 10, 29, 31, 400)]

    session_start = _run_step(threshold_days, entries)
    assert session_start is not None

    # The maintenance pass's own classification, expressed the way
    # ``_verification_pass`` expresses it.
    cutoff = _dt.now(timezone.utc) - timedelta(days=threshold_days)
    maintenance_stale = sum(
        1 for e in entries for a in e.assertions if a.last_verified_at is None or a.last_verified_at < cutoff
    )
    assert session_start["stale"] == maintenance_stale == 2


def test_assertion_health_returns_no_summary_when_the_config_cannot_resolve() -> None:
    """PRD-CORE-263-FR08 refuse-on-exception — no hardcoded fallback window."""
    from pathlib import Path
    from unittest.mock import patch

    from trw_mcp.tools._ceremony_degradations import DegradationCollector
    from trw_mcp.tools._ceremony_session_start_steps import step_assertion_health

    collector = DegradationCollector()
    with patch("trw_mcp.models.config.get_config", side_effect=RuntimeError("config unreadable")):
        result = step_assertion_health(Path("/nonexistent"), collector, None)

    assert result is None
    assert [item["step"] for item in collector.items] == ["assertion_health"]
