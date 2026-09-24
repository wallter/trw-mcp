"""PRD-CORE-231-FR02: explicit verification PERSISTS verification_status; recall reads it.

These tests run against a real ``SQLiteBackend`` and the real
``verify_assertions`` implementation — the whole point of FR02 is that the
verdict survives storage, which a mocked backend cannot prove.

NOT PORTED (PRD-CORE-280 slice e1): every test above ``test_keep_retrieval_
order_demotes_only_failed_evidence`` drives ``trw_memory.lifecycle.
verification_pass.run_verification_pass``/``persist_verification_outcome``
directly against a real ``SQLiteBackend`` (via the module-level ``backend``
fixture and ``_wire``, which patches ``memory_adapter.get_backend``).
``persist_verification_outcome`` takes a synchronous ``Store``
object, not the async, remote ``DaemonClient`` ``daemon_checkout`` provides,
so there is no daemon-route equivalent; ``fake_memory_store`` cannot stand in
either, since it does not implement real anchor/assertion verification.
These are left unchanged and unmigrated (still construct ``SQLiteBackend``
directly) — see the batch report. ``test_stale_verdict_survives_a_fresh_
connection`` (explicit close + reopen of the same sqlite file to prove
durability across a process restart) was DELETED as SQLite-internals-shaped
per the batch contract, not ported.

Everything from ``test_keep_retrieval_order_demotes_only_failed_evidence``
onward touches no memory store at all (plain dicts) and needed no change.
"""

from __future__ import annotations

import os
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
    from trw_memory.lifecycle.verification_pass import persist_verification_outcome, run_verification_pass

    from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir
    from trw_mcp.state.memory_adapter import get_backend

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


@pytest.mark.skipif(
    os.environ.get("TRW_E1_ORACLE") == "1",
    reason="BLOCKED-ON-E3: run_verification_pass/persist_verification_outcome need a synchronous SQLiteBackend/Store (via get_backend); daemon_checkout only exposes an async remote DaemonClient with no matching call, and fake_memory_store doesn't implement real anchor/assertion verification",
)
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


@pytest.mark.skipif(
    os.environ.get("TRW_E1_ORACLE") == "1",
    reason="BLOCKED-ON-E3: run_verification_pass/persist_verification_outcome need a synchronous SQLiteBackend/Store (via get_backend); daemon_checkout only exposes an async remote DaemonClient with no matching call, and fake_memory_store doesn't implement real anchor/assertion verification",
)
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


@pytest.mark.skipif(
    os.environ.get("TRW_E1_ORACLE") == "1",
    reason="BLOCKED-ON-E3: run_verification_pass/persist_verification_outcome need a synchronous SQLiteBackend/Store (via get_backend); daemon_checkout only exposes an async remote DaemonClient with no matching call, and fake_memory_store doesn't implement real anchor/assertion verification",
)
def test_persist_drift_warning_fires_when_the_write_is_lost(
    monkeypatch: pytest.MonkeyPatch,
    backend: SQLiteBackend,
    project: Path,
) -> None:
    """NFR02: a backend that silently drops the verdict is reported, not ignored."""
    from trw_memory.lifecycle.verification_pass import persist_verification_outcome, run_verification_pass

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


@pytest.mark.parametrize("result", [True, False, None])
@pytest.mark.skipif(
    os.environ.get("TRW_E1_ORACLE") == "1",
    reason="BLOCKED-ON-E3: run_verification_pass/persist_verification_outcome need a synchronous SQLiteBackend/Store (via get_backend); daemon_checkout only exposes an async remote DaemonClient with no matching call, and fake_memory_store doesn't implement real anchor/assertion verification",
)
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


def test_keep_retrieval_order_demotes_only_failed_evidence() -> None:
    """PRD-CORE-292: trw_recall keeps the pipeline's order; failed claims sink, stably."""
    from trw_mcp.tools._recall_assertion_verification import keep_retrieval_order

    rows = [{"id": i} for i in ("a", "b", "c", "d")]
    penalty = {"a": 0.0, "b": 0.5, "c": 0.0, "d": 0.5}
    ranked = keep_retrieval_order(rows, ["q"], 0.5, assertion_penalties=lambda r: penalty[str(r["id"])])
    assert [r["id"] for r in ranked] == ["a", "c", "b", "d"]
    unchanged = keep_retrieval_order(rows, ["q"], 0.5, assertion_penalties=lambda _r: 0.0)
    assert [r["id"] for r in unchanged] == ["a", "b", "c", "d"]


def _foreign_mix(monkeypatch):  # type: ignore[no-untyped-def]
    from trw_mcp.tools import _recall_order

    monkeypatch.setattr(
        _recall_order._origin_project, "is_attributable_to_this_project", lambda entry: entry["id"] != "foreign-strong"
    )
    return [{"id": "foreign-strong"}, {"id": "local-1"}, {"id": "local-2"}, {"id": "local-weak"}]


def test_pipeline_scores_drive_a_soft_foreign_penalty(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """PRD-CORE-282 via PRD-CORE-292: combined_score IS the pipeline's score, unscaled."""
    from trw_mcp.state._recall_signals import recall_signal_scope
    from trw_mcp.tools import _recall_order
    from trw_mcp.tools._recall_assertion_verification import keep_retrieval_order

    rows = _foreign_mix(monkeypatch)
    pipeline = {"foreign-strong": 1.0, "local-1": 0.9, "local-2": 0.2, "local-weak": 0.1}
    with recall_signal_scope("q") as signals:
        for row in rows:
            signals.bind_relevance(row, pipeline[str(row["id"])])
        ranked = keep_retrieval_order(rows, ["q"], 0.5, assertion_penalties=lambda _r: 0.0)
    assert {str(r["id"]): r["combined_score"] for r in ranked} == pipeline
    # Halved, the foreign row (0.5) still beats weaker locals but not the stronger one.
    ids = [r["id"] for r in _recall_order._apply_foreign_penalty(ranked)]
    assert ids == ["local-1", "foreign-strong", "local-2", "local-weak"], ids


def test_without_pipeline_scores_this_projects_rows_come_first(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Keyword fallback / ID lookups carry no score: no combined_score, local-first partition."""
    from trw_mcp.state._recall_signals import recall_signal_scope
    from trw_mcp.tools import _recall_order
    from trw_mcp.tools._recall_assertion_verification import keep_retrieval_order

    rows = _foreign_mix(monkeypatch)
    with recall_signal_scope("q") as signals:
        signals.bind_relevance(rows[0], 1.0)  # one scored row is not enough to stamp any
        ranked = keep_retrieval_order(rows, ["q"], 0.5, assertion_penalties=lambda _r: 0.0)
    assert all("combined_score" not in r for r in ranked)
    ids = [r["id"] for r in _recall_order._apply_foreign_penalty(ranked)]
    assert ids == ["local-1", "local-2", "local-weak", "foreign-strong"], ids


def test_anchor_invalidity_is_a_stable_demotion() -> None:
    from trw_mcp.tools._recall_assertion_verification import keep_retrieval_order

    rows = [{"id": "a"}, {"id": "stale", "anchor_validity": 0.0}, {"id": "b"}]
    ranked = keep_retrieval_order(rows, ["q"], 0.5, assertion_penalties=lambda _r: 0.0)
    assert [r["id"] for r in ranked] == ["a", "b", "stale"]
