"""CORE268 replaces lazy verification with explicit refresh and evidence-only recall.

The filesystem fail/correction and first_failed_at contracts remain real; only
who performs verification changes. Recall must never scan, persist or cache-fetch.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from trw_memory.models.memory import Assertion, AssertionType, MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._maintain_verify import run_maintain_verify
from trw_mcp.tools._recall_impl import _verify_assertions


@pytest.fixture()
def store(tmp_path: Path):
    backend = SQLiteBackend(tmp_path / "memory.db")
    backend.store(
        MemoryEntry(
            id="L-test",
            content="test claim",
            assertions=[Assertion(type=AssertionType.GREP_PRESENT, pattern="my_func", target="source.py")],
        )
    )
    yield backend, tmp_path
    backend.close()


def refresh(backend, root):
    return run_maintain_verify(
        backend,
        assertion_failure_penalty=0.15,
        assertion_stale_threshold_days=7,
        anchor_validity_verified_floor=0.8,
        batch_limit=1,
        project_root=root,
    )


def recall(entry, monkeypatch):
    with monkeypatch.context() as guard:

        def forbidden(*args, **kwargs):
            raise AssertionError("decision-time verification work")

        guard.setattr("trw_mcp.tools._verification_pass.run_verification_pass", forbidden)
        guard.setattr("trw_mcp.tools._verification_pass.persist_verification_outcome", forbidden)
        guard.setattr("trw_mcp.tools._verification_cache.warm_verified_verdict", forbidden)
        rank = MagicMock(side_effect=lambda rows, *a, **kw: rows)
        rows = _verify_assertions([entry], ["test"], TRWConfig(), rank)
        return rows[0], rank


def payload(backend):
    return backend.get("L-test", namespace="default").model_dump(mode="json")


def test_explicit_refresh_attaches_observation_without_query_write(store, monkeypatch):
    backend, root = store
    (root / "source.py").write_text("def my_func(): pass\n")
    refresh(backend, root)
    before = payload(backend)
    result, rank = recall(before, monkeypatch)
    assert result["verification_status"] == "last_known_pass"
    assert result["verification_evidence"]["current_tree_verified"] is False
    assert result["verification_evidence"]["assertions"][0]["checked_at"]
    assert payload(backend) == before
    rank.assert_not_called()


def test_no_assertions_are_unknown_without_scan(monkeypatch):
    result, rank = recall({"id": "L-none"}, monkeypatch)
    assert result["verification_status"] == "unknown"
    rank.assert_not_called()


def test_unresolvable_refresh_does_not_invent_observation(store, monkeypatch):
    backend, _ = store
    refresh(backend, None)
    result, rank = recall(payload(backend), monkeypatch)
    assert result["verification_status"] == "unknown"
    rank.assert_not_called()


def test_failure_first_failed_at_then_actual_correction(store, monkeypatch):
    backend, root = store
    (root / "source.py").write_text("missing = 1\n")
    refresh(backend, root)
    failed = payload(backend)
    assert failed["assertions"][0]["last_result"] is False
    assert failed["assertions"][0]["first_failed_at"]
    result, rank = recall(failed, monkeypatch)
    assert result["verification_status"] == "last_known_failure"
    assert rank.call_args.kwargs["assertion_penalties"](failed) == 0.15
    (root / "source.py").write_text("def my_func(): pass\n")
    # Repair is not observed until explicit refresh, and recall does not fake it.
    still_failed, _ = recall(payload(backend), monkeypatch)
    assert still_failed["verification_status"] == "last_known_failure"
    refresh(backend, root)
    corrected = payload(backend)
    assert corrected["assertions"][0]["first_failed_at"] is None
    assert corrected["assertions"][0]["last_result"] is True
    result, rank = recall(corrected, monkeypatch)
    assert result["verification_status"] == "last_known_pass"
    rank.assert_not_called()


def test_unknown_refresh_preserves_dated_failure(store, monkeypatch):
    backend, root = store
    (root / "source.py").write_text("missing = 1\n")
    refresh(backend, root)
    before = payload(backend)
    refresh(backend, None)
    after = payload(backend)
    assert after["assertions"] == before["assertions"]
    result, rank = recall(after, monkeypatch)
    assert result["verification_status"] == "last_known_failure"
    assert rank.called


@pytest.mark.parametrize("age", [1, 7200])
@pytest.mark.parametrize("passed", [True, False])
def test_fresh_and_expired_verdicts_never_trigger_inline_work(age, passed, monkeypatch):
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()
    entry = {"id": "L-age", "assertions": [{"last_result": passed, "last_verified_at": stamp}]}
    result, rank = recall(entry, monkeypatch)
    item = result["verification_evidence"]["assertions"][0]
    assert item["observation"] == ("pass" if passed else "failure")
    assert item["freshness"] == ("fresh" if age == 1 else "expired")
    assert rank.called is (not passed)
    assert result["verification_evidence"]["current_tree_verified"] is False


@pytest.mark.parametrize("stamp", [None, "", "bad", "2020-01-01T00:00:00", "2999-01-01T00:00:00Z"])
def test_undated_or_invalid_failures_do_not_manufacture_penalty(stamp, monkeypatch):
    result, rank = recall(
        {"id": "L-invalid", "assertions": [{"last_result": False, "last_verified_at": stamp}]}, monkeypatch
    )
    assert result["verification_status"] == "unknown"
    rank.assert_not_called()
