"""CORE268: real bounded maintenance traversal and preserved claim observations."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from trw_memory.models.memory import Anchor, Assertion, AssertionType, MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.tools._maintain_verify import run_maintain_verify
from trw_mcp.tools._verification_pass import run_verification_pass


def sweep(backend, root, namespace=None):
    return run_maintain_verify(
        backend,
        assertion_failure_penalty=0.15,
        assertion_stale_threshold_days=7,
        anchor_validity_verified_floor=0.8,
        batch_limit=1,
        project_root=root,
        namespace=namespace,
    )


def assertion():
    return Assertion(type=AssertionType.GREP_PRESENT, pattern="actual_symbol", target="fixture.py")


def test_all_pages_anchors_namespaces_and_historical_fields(tmp_path: Path):
    (tmp_path / "fixture.py").write_text("def actual_symbol():\n    return 1\n")
    backend = SQLiteBackend(tmp_path / "memory.db")
    try:
        for namespace in ("a", "b"):
            for lid in ("L-1", "L-2", "L-3"):
                backend.store(
                    MemoryEntry(
                        id=lid,
                        namespace=namespace,
                        content="fixture claim",
                        assertions=[assertion()],
                        q_value=0.25,
                        q_observations=9,
                        outcome_history=["historic"],
                    )
                )
        backend.store(
            MemoryEntry(
                id="L-anchor",
                namespace="b",
                content="anchor",
                anchors=[Anchor(file="fixture.py", symbol_name="actual_symbol")],
            )
        )
        assert len(backend.entries_with_assertions(namespace="b")) == 3
        first = sweep(backend, tmp_path, namespace="a")
        assert first.entries_processed == 3
        assert not backend.get("L-1", namespace="b").verification_checked_at
        all_namespaces = sweep(backend, tmp_path)
        assert all_namespaces.entries_processed == 7
        for namespace in ("a", "b"):
            for lid in ("L-1", "L-2", "L-3"):
                entry = backend.get(lid, namespace=namespace)
                assert entry.verification_checked_at
                assert (entry.q_value, entry.q_observations, entry.outcome_history) == (0.25, 9, ["historic"])
        assert backend.get("L-anchor", namespace="b").verification_checked_at
    finally:
        backend.close()


def test_persistence_failure_does_not_starve_later_page(tmp_path: Path):
    (tmp_path / "fixture.py").write_text("actual_symbol = 1\n")
    backend = SQLiteBackend(tmp_path / "memory.db")
    try:
        for lid in ("L-1", "L-2", "L-3"):
            backend.store(MemoryEntry(id=lid, content="claim", assertions=[assertion()]))
        calls = []

        class FailingFirst:
            def entries_with_assertions(self, **kwargs):
                result = backend.entries_with_assertions(**kwargs)
                calls.append((kwargs, len(result)))
                return result

            def update(self, lid, **kwargs):
                if lid == "L-1":
                    raise OSError("controlled persist failure")
                return backend.update(lid, **kwargs)

        result = sweep(FailingFirst(), tmp_path)
        assert result.entries_processed == 3
        assert result.persist_failures == 1
        assert backend.get("L-3", namespace="default").verification_checked_at
        assert all(size <= 1 for _, size in calls)
        assert [c[0]["after"] for c in calls] == [None, ("default", "L-1"), ("default", "L-2"), ("default", "L-3")]
    finally:
        backend.close()


def test_unknown_first_page_and_real_correction(tmp_path: Path):
    backend = SQLiteBackend(tmp_path / "memory.db")
    try:
        for lid in ("L-1", "L-2", "L-3"):
            backend.store(MemoryEntry(id=lid, content="claim", assertions=[assertion()]))
        assert sweep(backend, None).entries_processed == 3
        assert not backend.get("L-3", namespace="default").verification_checked_at
        (tmp_path / "fixture.py").write_text("missing = 1\n")
        sweep(backend, tmp_path)
        assert backend.get("L-3", namespace="default").assertions[0].last_result is False
        (tmp_path / "fixture.py").write_text("actual_symbol = 1\n")
        sweep(backend, tmp_path)
        final = backend.get("L-3", namespace="default")
        assert final.assertions[0].last_result is True
        assert final.assertions[0].first_failed_at is None
    finally:
        backend.close()


def test_mixed_unknown_refresh_preserves_previous_observation(tmp_path: Path, monkeypatch):
    import trw_memory.lifecycle.verification as verifier
    from trw_memory.models.memory import AssertionResult

    old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    failed = assertion().model_copy(
        update={"last_result": False, "last_verified_at": old, "first_failed_at": old, "last_evidence": "prior failure"}
    )
    clean = assertion()
    monkeypatch.setattr(
        verifier,
        "verify_assertions",
        lambda *_: [
            AssertionResult(type=AssertionType.GREP_PRESENT, passed=None, evidence="unavailable"),
            AssertionResult(type=AssertionType.GREP_PRESENT, passed=True, evidence="present"),
        ],
    )
    outcome = run_verification_pass(
        "L-mixed",
        [failed.model_dump(mode="json"), clean.model_dump(mode="json")],
        [],
        assertion_failure_penalty=0.15,
        assertion_stale_threshold_days=7,
        anchor_validity_verified_floor=0.8,
        project_root=tmp_path,
    )
    assert outcome.verifiable
    assert outcome.updated_assertions[0]["last_result"] is False
    assert outcome.updated_assertions[0]["last_verified_at"] == old.isoformat().replace("+00:00", "Z")
    assert outcome.updated_assertions[0]["last_evidence"] == "prior failure"
    assert outcome.verification_status != "verified"


@pytest.mark.parametrize("corrupt_ids", [("L-1",), ("L-1", "L-2")])
def test_malformed_raw_pages_do_not_hide_later_valid_claims(tmp_path: Path, corrupt_ids):
    """Actual SQLite decode drops must not be mistaken for candidate exhaustion."""
    (tmp_path / "fixture.py").write_text("actual_symbol = 1\n")
    backend = SQLiteBackend(tmp_path / "memory.db")
    try:
        for lid in ("L-1", "L-2", "L-3", "L-4"):
            backend.store(MemoryEntry(id=lid, content="claim", assertions=[assertion()]))
        with backend._lock:
            for lid in corrupt_ids:
                backend._conn.execute("UPDATE memories SET importance = 'invalid' WHERE id = ?", (lid,))
            backend._conn.commit()
        # Default summary keeps its existing raw-limit semantics.
        legacy = backend.entries_with_assertions(limit=2)
        assert len(legacy) <= 2
        pages = []
        cursor = None
        while True:
            page = backend.entries_with_assertions(limit=2, include_anchors=True, after=cursor)
            assert len(page) <= 2
            if not page:
                break
            pages.extend(entry.id for entry in page)
            cursor = (page[-1].namespace, page[-1].id)
        assert pages == [lid for lid in ("L-1", "L-2", "L-3", "L-4") if lid not in corrupt_ids]
        outcome = run_maintain_verify(
            backend,
            assertion_failure_penalty=0.15,
            assertion_stale_threshold_days=7,
            anchor_validity_verified_floor=0.8,
            batch_limit=2,
            project_root=tmp_path,
        )
        assert outcome.entries_processed == 4 - len(corrupt_ids)
        assert backend.get("L-4", namespace="default").verification_checked_at
        assert backend.get("L-3", namespace="default").assertions[0].last_result is True
    finally:
        backend.close()
