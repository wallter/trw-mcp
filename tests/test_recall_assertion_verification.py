"""PRD-CORE-231-FR02: explicit verification PERSISTS verification_status; recall reads it.

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


def _refresh_evidence(entries: list[dict[str, object]], _tokens: list[str], config: TRWConfig, _ranker: Any) -> None:
    """Explicit maintenance-owner exercise; recall no longer refreshes evidence."""
    from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir
    from trw_mcp.state.memory_adapter import get_backend
    from trw_mcp.tools._verification_pass import persist_verification_outcome, run_verification_pass

    for entry in entries:
        outcome = run_verification_pass(
            str(entry["id"]),
            entry.get("assertions", []),
            entry.get("anchors", []),
            assertion_failure_penalty=config.assertion_failure_penalty,
            assertion_stale_threshold_days=config.assertion_stale_threshold_days,
            anchor_validity_verified_floor=config.anchor_validity_verified_floor,
            project_root=resolve_project_root(),
        )
        persist_verification_outcome(get_backend(resolve_trw_dir()), outcome)


def test_stale_write_back_and_clear(
    monkeypatch: pytest.MonkeyPatch,
    backend: SQLiteBackend,
    project: Path,
) -> None:
    """A persistently-failing claim is written 'stale', then cleared when it re-passes."""
    from tests.test_recall_assertion_verification import _refresh_evidence as _verify_assertions

    _wire(monkeypatch, backend, project)
    config = TRWConfig()
    old_failure = datetime.now(timezone.utc) - timedelta(days=config.assertion_stale_threshold_days + 15)

    _store(backend, "L-stale", [_failing_assertion(old_failure)])
    _verify_assertions([_learning("L-stale", [_failing_assertion(old_failure)])], ["q"], config, _rank)

    persisted = backend.get("L-stale", namespace="default")
    assert persisted is not None
    assert persisted.verification_status == "stale"

    # Now the assertion re-passes: the SAME call must clear the stale verdict.
    # PRD-CORE-244 FR03: clearing now lands on the positive value rather than on
    # None. This assertion previously required the absence of any verdict, which
    # is what made "healthy" and "never examined" the same stored state.
    _verify_assertions([_learning("L-stale", [_passing_assertion(old_failure)])], ["q"], config, _rank)

    recleared = backend.get("L-stale", namespace="default")
    assert recleared is not None
    assert recleared.verification_status == "verified"
    assert recleared.verification_checked_at != ""


def test_recent_failure_is_not_persisted_stale(
    monkeypatch: pytest.MonkeyPatch,
    backend: SQLiteBackend,
    project: Path,
) -> None:
    """A failure younger than the threshold records no adverse verdict."""
    from tests.test_recall_assertion_verification import _refresh_evidence as _verify_assertions

    _wire(monkeypatch, backend, project)
    config = TRWConfig()
    recent = datetime.now(timezone.utc) - timedelta(days=1)

    _store(backend, "L-recent", [_failing_assertion(recent)])
    _verify_assertions([_learning("L-recent", [_failing_assertion(recent)])], ["q"], config, _rank)

    persisted = backend.get("L-recent", namespace="default")
    assert persisted is not None
    assert persisted.verification_status is None


def test_stale_verdict_survives_a_fresh_connection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    project: Path,
) -> None:
    """US-002: the verdict is visible from a brand-new backend (process restart)."""
    from tests.test_recall_assertion_verification import _refresh_evidence as _verify_assertions

    db_path = tmp_path / "store" / "memory.db"
    backend = SQLiteBackend(db_path)
    _wire(monkeypatch, backend, project)
    config = TRWConfig()
    old_failure = datetime.now(timezone.utc) - timedelta(days=config.assertion_stale_threshold_days + 5)

    _store(backend, "L-restart", [_failing_assertion(old_failure)])
    _verify_assertions([_learning("L-restart", [_failing_assertion(old_failure)])], ["q"], config, _rank)
    backend.close()

    reopened = SQLiteBackend(db_path)
    entry = reopened.get("L-restart", namespace="default")
    assert entry is not None
    assert entry.verification_status == "stale"


def test_no_persist_drift_warning_on_the_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    backend: SQLiteBackend,
    project: Path,
) -> None:
    """NFR02: the self-check stays silent when the write actually landed."""
    from tests.test_recall_assertion_verification import _refresh_evidence as _verify_assertions

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
        anchor_validity_verified_floor=config.anchor_validity_verified_floor,
        project_root=project,
    )
    assert outcome.verification_status == "stale"

    class _DroppingBackend:
        """Persists everything EXCEPT the new verdict — the FR02 bug class.

        The signature MIRRORS ``SQLiteBackend.update`` exactly, including the
        keyword-only ``namespace``. A double that accepted any signature is what
        hid PRD-CORE-245's missed ``namespace=`` here: the real call raised a
        TypeError that ``persist_verification_outcome``'s best-effort handler
        swallowed, so the verdict never landed and this very tripwire could
        never fire.
        """

        def update(self, entry_id: str, *, namespace: str, **fields: object) -> MemoryEntry | None:
            fields.pop("verification_status", None)
            return backend.update(entry_id, namespace=namespace, **fields)

    with structlog.testing.capture_logs() as logs:
        persist_verification_outcome(_DroppingBackend(), outcome)

    drift = [entry for entry in logs if entry["event"] == "verification_status_persist_drift"]
    assert len(drift) == 1
    assert drift[0]["computed"] == "stale"
    assert drift[0]["persisted"] is None


# ---------------------------------------------------------------------------
# PRD-CORE-244 FR04 — a contradiction becomes a per-entry negative Q observation
# ---------------------------------------------------------------------------


def _store_learning_for_q(trw_dir: Path, entry_id: str) -> None:
    """Write the YAML sidecar the Q-update path reads and rewrites."""
    import yaml

    entries_dir = trw_dir / "learnings" / "entries"
    entries_dir.mkdir(parents=True, exist_ok=True)
    (entries_dir / f"{entry_id}.yaml").write_text(
        yaml.safe_dump(
            {
                "id": entry_id,
                "summary": "claim under test",
                "impact": 0.6,
                "q_value": 0.6,
                "q_observations": 4,
                "recurrence": 1,
                "outcome_history": [],
                "status": "active",
            }
        ),
        encoding="utf-8",
    )


def _read_q(trw_dir: Path, entry_id: str) -> dict[str, object]:
    """Read the entry through the SAME lookup the reward path uses.

    Reading the YAML sidecar directly would compare against a different source
    than the code writes through: ``_default_lookup_entry`` is SQLite-primary,
    so a sidecar-only fixture value is never what the penalty starts from.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.scoring._correlation import _default_lookup_entry

    cfg = get_config()
    _path, data = _default_lookup_entry(entry_id, trw_dir, trw_dir / cfg.learnings_dir / cfg.entries_dir)
    assert data is not None
    return data


@pytest.mark.parametrize("result", [True, False, None])
def test_recall_does_not_mutate_claims_or_q(
    monkeypatch: pytest.MonkeyPatch, backend: SQLiteBackend, project: Path, result: bool | None
) -> None:
    from trw_mcp.tools._recall_impl import _verify_assertions

    _wire(monkeypatch, backend, project)
    assertion = _failing_assertion(None)
    assertion.last_result = result
    assertion.last_verified_at = datetime.now(timezone.utc)
    _store(backend, "L-observed", [assertion])
    before = backend.get("L-observed", namespace="default").model_dump(mode="json")
    for _ in range(5):
        _verify_assertions([_learning("L-observed", [assertion])], [], TRWConfig(), _rank)
    after = backend.get("L-observed", namespace="default").model_dump(mode="json")
    assert after == before


def test_the_verdict_actually_lands_through_the_real_backend(
    monkeypatch: pytest.MonkeyPatch,
    backend: SQLiteBackend,
    project: Path,
) -> None:
    """PRD-CORE-245 FR03 regression: persist writes through a REAL SQLiteBackend.

    Every other test here hands ``persist_verification_outcome`` a double. That
    is what let a missing ``namespace=`` survive: against the real signature the
    call raises TypeError, and the best-effort handler at the call site turns
    that into a silent "no verdict persisted". This test uses no double at all.
    """
    from trw_mcp.tools._verification_pass import persist_verification_outcome, run_verification_pass

    _wire(monkeypatch, backend, project)
    config = TRWConfig()
    old_failure = datetime.now(timezone.utc) - timedelta(days=config.assertion_stale_threshold_days + 5)
    _store(backend, "L-lands", [_failing_assertion(old_failure)])

    outcome = run_verification_pass(
        "L-lands",
        [_failing_assertion(old_failure).model_dump(mode="json")],
        [],
        assertion_failure_penalty=config.assertion_failure_penalty,
        assertion_stale_threshold_days=config.assertion_stale_threshold_days,
        anchor_validity_verified_floor=config.anchor_validity_verified_floor,
        project_root=project,
    )
    assert outcome.verification_status == "stale"

    with structlog.testing.capture_logs() as logs:
        assert persist_verification_outcome(backend, outcome) is True

    stored = backend.get("L-lands", namespace=outcome.namespace)
    assert stored is not None
    assert stored.verification_status == "stale", "the verdict must be readable back from storage"
    assert not [entry for entry in logs if entry["event"] == "verification_status_persist_drift"]
    assert not [entry for entry in logs if entry["event"] == "assertion_result_persist_failed"]
