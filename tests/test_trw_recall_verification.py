"""CORE268 replaces lazy verification with explicit refresh and evidence-only recall.

The filesystem fail/correction and first_failed_at contracts remain real; only
who performs verification changes. Recall must never scan, persist or cache-fetch.

The refresh runs through the checkout's store (``MemoryStore.verify``), which a
daemon checkout sends to ``memory_verify``; recall reads the refreshed row back.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from trw_memory.lifecycle.verification_pass import VerifySettings
from trw_memory.models.memory import Assertion, AssertionType

from tests._memory_fixtures import DaemonCheckout
from trw_mcp.models.config import TRWConfig
from trw_mcp.state._store_selection import MemoryStore, selected_store
from trw_mcp.tools._recall_impl import _verify_assertions


@dataclass(frozen=True)
class _Checkout:
    memory: MemoryStore
    namespace: str


@pytest.fixture()
def store(daemon_checkout: DaemonCheckout) -> tuple[_Checkout, Path]:
    memory, namespace = selected_store(daemon_checkout.trw_dir)
    assertion = Assertion(type=AssertionType.GREP_PRESENT, pattern="my_func", target="source.py")
    memory.put("test claim", namespace, {"entry_id": "L-test", "assertions": [assertion]})
    # The daemon verifies against the root the checkout's grant names.
    return _Checkout(memory, namespace), daemon_checkout.trw_dir.parent


def refresh(checkout: _Checkout, root: Path | None) -> None:
    settings = VerifySettings(
        assertion_failure_penalty=0.15,
        assertion_stale_threshold_days=7,
        anchor_validity_verified_floor=0.8,
        batch_limit=1,
    )
    checkout.memory.verify(checkout.namespace, root, settings)


def recall(entry, monkeypatch):
    with monkeypatch.context() as guard:

        def forbidden(*args, **kwargs):
            raise AssertionError("decision-time verification work")

        guard.setattr("trw_memory.lifecycle.verification_pass.run_verification_pass", forbidden)
        guard.setattr("trw_memory.lifecycle.verification_pass.persist_verification_outcome", forbidden)
        guard.setattr("trw_mcp.tools._verification_cache.warm_verified_verdict", forbidden)
        rank = MagicMock(side_effect=lambda rows, *a, **kw: rows)
        rows = _verify_assertions([entry], ["test"], TRWConfig(), rank)
        return rows[0], rank


def payload(checkout: _Checkout) -> dict[str, object]:
    entry = checkout.memory.get("L-test")
    assert entry is not None
    return entry.model_dump(mode="json")


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


def test_a_never_observed_assertion_is_unknown_without_scan(monkeypatch):
    # A refresh that could not resolve the tree leaves the assertion unobserved;
    # trw-memory tests that the pass records nothing (test_verification_pass_without_root).
    entry = {"id": "L-unseen", "assertions": [{"type": "grep_present", "pattern": "x", "target": "a.py"}]}
    result, rank = recall(entry, monkeypatch)
    assert result["verification_status"] == "unknown"
    rank.assert_not_called()


def test_no_assertions_are_unknown_without_scan(monkeypatch):
    result, rank = recall({"id": "L-none"}, monkeypatch)
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
