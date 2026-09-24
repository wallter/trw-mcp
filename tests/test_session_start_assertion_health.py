"""The session_start assertion-health step reads the store's summary (PRD-CORE-086 FR07).

The counting itself lives with the rows, in ``trw_memory.lifecycle.verification_pass``
(its cases are ``trw-memory/tests/test_assertion_health.py``). Here the step must pass
the configured stale window through, scope the summary to its project namespace, and
fail open with a typed degradation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from trw_memory.models.memory import Assertion, AssertionType, MemoryEntry

from tests._memory_fixtures import FAKE_NAMESPACE
from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._ceremony_degradations import DegradationCollector
from trw_mcp.tools._ceremony_session_start_steps import step_assertion_health


def _verified_days_ago(store: FakeMemoryStore, entry_id: str, days: int, namespace: str = FAKE_NAMESPACE) -> None:
    moment = datetime.now(timezone.utc) - timedelta(days=days)
    assertion = Assertion(type=AssertionType.GLOB_EXISTS, target="x.py", last_result=True, last_verified_at=moment)
    store.rows[(namespace, entry_id)] = MemoryEntry(
        id=entry_id, content=entry_id, namespace=namespace, assertions=[assertion]
    )


def _run(stale_days: int, trw_dir: Path) -> dict[str, int] | None:
    return step_assertion_health(trw_dir, None, TRWConfig(assertion_stale_threshold_days=stale_days))  # type: ignore[call-arg]


def test_assertion_health_uses_the_configured_stale_threshold(
    fake_memory_store: FakeMemoryStore, tmp_path: Path
) -> None:
    """PRD-CORE-263-FR08 — a 10-day-old verdict is fresh at the 30-day default and stale at 7."""
    _verified_days_ago(fake_memory_store, "L-ten", 10)

    assert _run(30, tmp_path) == {"passing": 1, "failing": 0, "stale": 0, "unverifiable": 0, "total": 1}
    assert _run(7, tmp_path) == {"passing": 0, "failing": 0, "stale": 1, "unverifiable": 0, "total": 1}
    assert ("assertion_health", (FAKE_NAMESPACE, 30)) in fake_memory_store.calls


def test_assertion_health_agrees_with_the_maintenance_pass_on_the_same_store(
    fake_memory_store: FakeMemoryStore, tmp_path: Path
) -> None:
    """PRD-CORE-263-FR08 — session start reads the one threshold the sweep reads."""
    threshold_days = TRWConfig().assertion_stale_threshold_days
    for days in (1, 10, 29, 31, 400):
        _verified_days_ago(fake_memory_store, f"L-{days}", days)

    summary = _run(threshold_days, tmp_path)

    assert summary is not None
    assert summary["stale"] == sum(1 for days in (1, 10, 29, 31, 400) if days > threshold_days) == 2


def test_assertion_health_counts_only_the_project_namespace(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    _verified_days_ago(fake_memory_store, "L-elsewhere", 1, namespace="project:other")

    assert _run(30, tmp_path) is None


def test_assertion_health_returns_no_summary_when_the_config_cannot_resolve() -> None:
    """PRD-CORE-263-FR08 refuse-on-exception — no hardcoded fallback window."""
    collector = DegradationCollector()
    with patch("trw_mcp.models.config.get_config", side_effect=RuntimeError("config unreadable")):
        result = step_assertion_health(Path("/nonexistent"), collector, None)

    assert result is None
    assert [item["step"] for item in collector.items] == ["assertion_health"]


def test_an_unavailable_store_is_a_degradation_not_a_summary(
    fake_memory_store: FakeMemoryStore, tmp_path: Path
) -> None:
    """PRD-CORE-086 NFR — the step fails open, and says so."""
    from trw_mcp.state._store_selection import StoreUnavailableError

    def unavailable(_namespace: str, _stale_days: int) -> None:
        raise StoreUnavailableError("daemon down")

    fake_memory_store.assertion_health = unavailable  # type: ignore[method-assign]
    collector = DegradationCollector()

    assert step_assertion_health(tmp_path, collector, TRWConfig()) is None
    assert [item["step"] for item in collector.items] == ["assertion_health"]
