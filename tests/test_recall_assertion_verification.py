"""PRD-CORE-231-FR02: the recall verification pass PERSISTS verification_status.

These tests run against a real ``SQLiteBackend`` and the real
``verify_assertions`` implementation — the whole point of FR02 is that the
verdict survives storage, which a mocked backend cannot prove.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import structlog
from trw_memory.models.memory import Assertion, AssertionType, MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.models.config import TRWConfig


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A minimal project tree containing one source file."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def live_symbol() -> None:\n    return None\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def backend(tmp_path: Path) -> SQLiteBackend:
    return SQLiteBackend(tmp_path / "store" / "memory.db")


def _wire(monkeypatch: pytest.MonkeyPatch, backend: SQLiteBackend, project: Path) -> None:
    """Point the verification pass at the real test backend + project root."""
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: project / ".trw")
    monkeypatch.setattr("trw_mcp.state.memory_adapter.get_backend", lambda _trw_dir: backend)


def _store(backend: SQLiteBackend, entry_id: str, assertions: list[Assertion]) -> None:
    now = datetime.now(timezone.utc)
    backend.store(
        MemoryEntry(id=entry_id, content="claim under test", created_at=now, updated_at=now, assertions=assertions)
    )


def _failing_assertion(first_failed_at: datetime | None) -> Assertion:
    """An assertion that cannot pass — the pattern is absent from the tree."""
    return Assertion(
        type=AssertionType.GREP_PRESENT,
        pattern="symbol_that_was_deleted",
        target="**/*.py",
        first_failed_at=first_failed_at,
    )


def _passing_assertion(first_failed_at: datetime | None) -> Assertion:
    return Assertion(
        type=AssertionType.GREP_PRESENT,
        pattern="live_symbol",
        target="**/*.py",
        first_failed_at=first_failed_at,
    )


def _learning(entry_id: str, assertions: list[Assertion]) -> dict[str, object]:
    return {
        "id": entry_id,
        "summary": "claim under test",
        "assertions": [a.model_dump(mode="json") for a in assertions],
    }


def _rank(entries: list[dict[str, object]], *_args: Any, **_kwargs: Any) -> list[dict[str, object]]:
    return entries


def test_stale_write_back_and_clear(
    monkeypatch: pytest.MonkeyPatch,
    backend: SQLiteBackend,
    project: Path,
) -> None:
    """A persistently-failing claim is written 'stale', then cleared when it re-passes."""
    from trw_mcp.tools._recall_impl import _verify_assertions

    _wire(monkeypatch, backend, project)
    config = TRWConfig()
    old_failure = datetime.now(timezone.utc) - timedelta(days=config.assertion_stale_threshold_days + 15)

    _store(backend, "L-stale", [_failing_assertion(old_failure)])
    result = _verify_assertions([_learning("L-stale", [_failing_assertion(old_failure)])], ["q"], config, _rank)

    assert result[0]["verification_status"] == "stale"
    persisted = backend.get("L-stale")
    assert persisted is not None
    assert persisted.verification_status == "stale"

    # Now the assertion re-passes: the SAME call must clear the verdict.
    cleared = _verify_assertions([_learning("L-stale", [_passing_assertion(old_failure)])], ["q"], config, _rank)

    assert "verification_status" not in cleared[0]
    recleared = backend.get("L-stale")
    assert recleared is not None
    assert recleared.verification_status is None


def test_recent_failure_is_not_persisted_stale(
    monkeypatch: pytest.MonkeyPatch,
    backend: SQLiteBackend,
    project: Path,
) -> None:
    """A failure younger than the threshold records no adverse verdict."""
    from trw_mcp.tools._recall_impl import _verify_assertions

    _wire(monkeypatch, backend, project)
    config = TRWConfig()
    recent = datetime.now(timezone.utc) - timedelta(days=1)

    _store(backend, "L-recent", [_failing_assertion(recent)])
    _verify_assertions([_learning("L-recent", [_failing_assertion(recent)])], ["q"], config, _rank)

    persisted = backend.get("L-recent")
    assert persisted is not None
    assert persisted.verification_status is None


def test_stale_verdict_survives_a_fresh_connection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    project: Path,
) -> None:
    """US-002: the verdict is visible from a brand-new backend (process restart)."""
    from trw_mcp.tools._recall_impl import _verify_assertions

    db_path = tmp_path / "store" / "memory.db"
    backend = SQLiteBackend(db_path)
    _wire(monkeypatch, backend, project)
    config = TRWConfig()
    old_failure = datetime.now(timezone.utc) - timedelta(days=config.assertion_stale_threshold_days + 5)

    _store(backend, "L-restart", [_failing_assertion(old_failure)])
    _verify_assertions([_learning("L-restart", [_failing_assertion(old_failure)])], ["q"], config, _rank)
    backend.close()

    reopened = SQLiteBackend(db_path)
    entry = reopened.get("L-restart")
    assert entry is not None
    assert entry.verification_status == "stale"


def test_no_persist_drift_warning_on_the_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    backend: SQLiteBackend,
    project: Path,
) -> None:
    """NFR02: the self-check stays silent when the write actually landed."""
    from trw_mcp.tools._recall_impl import _verify_assertions

    _wire(monkeypatch, backend, project)
    config = TRWConfig()
    old_failure = datetime.now(timezone.utc) - timedelta(days=config.assertion_stale_threshold_days + 5)
    _store(backend, "L-nodrift", [_failing_assertion(old_failure)])

    with structlog.testing.capture_logs() as logs:
        _verify_assertions([_learning("L-nodrift", [_failing_assertion(old_failure)])], ["q"], config, _rank)

    assert [entry for entry in logs if entry["event"] == "verification_status_persist_drift"] == []


def test_persist_drift_warning_fires_when_the_write_is_lost(
    monkeypatch: pytest.MonkeyPatch,
    backend: SQLiteBackend,
    project: Path,
) -> None:
    """NFR02: a backend that silently drops the verdict is reported, not ignored."""
    from trw_mcp.tools._verification_pass import persist_verification_outcome, run_verification_pass

    _wire(monkeypatch, backend, project)
    config = TRWConfig()
    old_failure = datetime.now(timezone.utc) - timedelta(days=config.assertion_stale_threshold_days + 5)
    _store(backend, "L-drift", [_failing_assertion(old_failure)])

    outcome = run_verification_pass(
        "L-drift",
        [_failing_assertion(old_failure).model_dump(mode="json")],
        [],
        assertion_failure_penalty=config.assertion_failure_penalty,
        assertion_stale_threshold_days=config.assertion_stale_threshold_days,
        project_root=project,
    )
    assert outcome.verification_status == "stale"

    class _DroppingBackend:
        """Persists everything EXCEPT the new verdict — the FR02 bug class."""

        def update(self, entry_id: str, **fields: object) -> MemoryEntry | None:
            fields.pop("verification_status", None)
            return backend.update(entry_id, **fields)

    with structlog.testing.capture_logs() as logs:
        persist_verification_outcome(_DroppingBackend(), outcome)

    drift = [entry for entry in logs if entry["event"] == "verification_status_persist_drift"]
    assert len(drift) == 1
    assert drift[0]["computed"] == "stale"
    assert drift[0]["persisted"] is None
