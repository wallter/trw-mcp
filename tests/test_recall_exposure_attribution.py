"""Recall exposure must describe returned records, not prefetched candidates.

Candidate acquisition is controlled; production selection, access counters and
the durable recall receipt writer execute against a real store.

PRD-CORE-280: ``record_surfaced`` (the access_fn ``execute_recall`` calls to
increment ``recall_count``/``access_count`` on the SURFACED rows only) now
routes through ``_store_selection.selected_store(trw_dir)``, so every test in
this file drives the real daemon-backed checkout (``exposure_store`` /
``daemon_checkout``) end to end -- no legacy ``get_backend``-based fixture.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from tests._memory_fixtures import DaemonCheckout
from trw_mcp.models.config import TRWConfig
from trw_mcp.scoring._recall_window import correlate_recalls
from trw_mcp.state.memory_adapter import store_learning
from trw_mcp.tools import _recall_impl


@pytest.fixture
def exposure_store(daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch) -> DaemonCheckout:
    monkeypatch.setenv("TRW_EMBEDDINGS_ENABLED", "false")
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    monkeypatch.setattr(_recall_impl, "build_recall_context", lambda *a, **kw: None)
    monkeypatch.setattr(_recall_impl, "_augment_with_remote", lambda query, rows: (rows, None))
    monkeypatch.setattr("trw_mcp.state.recall_tracking.resolve_trw_dir", lambda: daemon_checkout.trw_dir)
    return daemon_checkout


def _get(checkout: DaemonCheckout, entry_id: str, namespace: str) -> dict[str, object] | None:
    result = asyncio.run(checkout.client.get(entry_id, namespace))
    if result.get("status") != "ok":
        return None
    return result["entry"]


def _candidates(checkout: DaemonCheckout, *, duplicate: bool = False) -> list[dict[str, object]]:
    summaries = [
        "Database migration requires schema verification",
        "Database connection pool requires timeout limits",
        "Database backup needs retention expiration policy",
    ]
    if duplicate:
        summaries[1] = summaries[0]
    entries: list[dict[str, object]] = []
    for index, summary in enumerate(summaries):
        lid = f"L-exposure{index}"
        store_learning(checkout.trw_dir, lid, summary, "")
        entries.append({"id": lid, "summary": summary, "impact": 0.8 - index * 0.2})
    return entries


def _assert_exposure(checkout: DaemonCheckout, returned: list[dict[str, object]]) -> None:
    expected = [str(row["id"]) for row in returned]
    receipts = checkout.trw_dir / "logs" / "recall_tracking.jsonl"
    tracked = (
        [json.loads(line)["learning_id"] for line in receipts.read_text().splitlines()] if receipts.exists() else []
    )
    assert tracked == expected
    assert set(correlate_recalls(checkout.trw_dir, 5, scope="window")) == set(expected)
    for index in range(3):
        lid = f"L-exposure{index}"
        row = _get(checkout, lid, checkout.namespace)
        assert row is not None
        assert row["recall_count"] == int(lid in expected)
        assert row["access_count"] == int(lid in expected)


def test_reviewer_does_not_write_exposure(exposure_store: DaemonCheckout, monkeypatch: pytest.MonkeyPatch) -> None:
    entries = _candidates(exposure_store)
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    result = _recall_impl.execute_recall(
        "database",
        exposure_store.trw_dir,
        TRWConfig(embeddings_enabled=False),
        max_results=1,
        _adapter_recall=lambda *a, **kw: entries,
    )
    assert len(result["learnings"]) == 1
    _assert_exposure(exposure_store, [])

    surface_path = exposure_store.trw_dir / "logs" / "surface_tracking.jsonl"
    assert not surface_path.exists() or not surface_path.read_text().strip()


def test_failed_response_preparation_does_not_record_exposure(
    exposure_store: DaemonCheckout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRD-CORE-294 FR01 deleted ``_collect_context`` (and the ``context``
    failure-stage parametrize case that patched it) along with
    ``_search_patterns``; only the ``_verify_assertions`` failure path remains.
    """
    entries = _candidates(exposure_store)

    def fail_verification(*args: object, **kwargs: object) -> list[dict[str, object]]:
        raise RuntimeError("verification aborted response")

    monkeypatch.setattr(_recall_impl, "_verify_assertions", fail_verification)
    with pytest.raises(RuntimeError, match="verification aborted"):
        _recall_impl.execute_recall(
            "database",
            exposure_store.trw_dir,
            TRWConfig(embeddings_enabled=False),
            max_results=1,
            _adapter_recall=lambda *a, **kw: entries,
        )
    _assert_exposure(exposure_store, [])

    for filename in ("surface_tracking.jsonl",):
        log_path = exposure_store.trw_dir / "logs" / filename
        assert not log_path.exists() or not log_path.read_text().strip()


# test_injected_access_adapter_retains_two_argument_contract DELETED
# (PRD-CORE-280): pinned execute_recall's injectable `_adapter_update_access`
# two-argument seam. That kwarg is gone -- execute_recall now looks up
# memory_adapter.record_surfaced at call time (not an injectable parameter),
# so there is nothing left to inject a legacy two-arg double into. Equivalent
# behaviour (the surfaced ids are what gets counted) is covered above by
# `_assert_exposure`'s recall_count/access_count checks.


@pytest.mark.parametrize("mode", ["default", "dedup", "empty"])
def test_only_returned_entries_receive_exposure(exposure_store: DaemonCheckout, mode: str) -> None:
    entries = _candidates(exposure_store, duplicate=mode == "dedup")
    if mode == "empty":
        entries = []
    result = _recall_impl.execute_recall(
        "database",
        exposure_store.trw_dir,
        TRWConfig(embeddings_enabled=False),
        max_results=10 if mode == "dedup" else 1,
        _adapter_recall=lambda *a, **kw: entries,
    )
    returned = result["learnings"]
    assert len(returned) < 3
    if mode != "empty":
        assert returned
    _assert_exposure(exposure_store, returned)


def test_remote_selection_not_local_prefetch_drives_tracking(
    exposure_store: DaemonCheckout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = _candidates(exposure_store)
    remote = {"id": "L-remote", "summary": "Database migration", "impact": 1.0}
    monkeypatch.setattr(_recall_impl, "_augment_with_remote", lambda query, rows: ([remote], None))
    result = _recall_impl.execute_recall(
        "database",
        exposure_store.trw_dir,
        TRWConfig(embeddings_enabled=False),
        max_results=1,
        _adapter_recall=lambda *a, **kw: entries,
    )
    assert result["learnings"][0]["id"] == "L-remote"
    _assert_exposure(exposure_store, result["learnings"])


def test_real_acquisition_and_receipts_agree(exposure_store: DaemonCheckout) -> None:
    """No acquisition or attribution doubles: real store search to receipts."""
    _candidates(exposure_store)
    result = _recall_impl.execute_recall(
        "database",
        exposure_store.trw_dir,
        TRWConfig(embeddings_enabled=False),
        max_results=1,
    )
    assert len(result["learnings"]) == 1
    _assert_exposure(exposure_store, result["learnings"])


def test_selected_records_increment_only_their_real_owning_stores(
    exposure_store: DaemonCheckout,
) -> None:
    from trw_mcp.state._tier_routing import USER_NAMESPACE

    entries = []
    namespaces = {"project": exposure_store.namespace, "user": USER_NAMESPACE}
    for tier, namespace in namespaces.items():
        for selected in (True, False):
            lid = f"L-{tier}-{'selected' if selected else 'discarded'}"
            summary = {
                ("project", True): "Database migration verifies schema constraints",
                ("project", False): "Filesystem backup retention uses expiration dates",
                ("user", True): "Database connection pooling requires idle timeout limits",
                ("user", False): "Filesystem credentials rotate using deployment secrets",
            }[tier, selected]
            asyncio.run(exposure_store.client.store(summary, namespace, entry_id=lid, detail=""))
            entries.append({"id": lid, "summary": summary, "impact": 0.9 if selected else 0.1})
    # The retrieval pipeline returns relevance order (PRD-CORE-292: execute_recall keeps
    # it rather than re-ranking), so the fake adapter does too: matching rows first.
    entries.sort(key=lambda entry: "selected" not in str(entry["id"]).split("-")[-1])
    result = _recall_impl.execute_recall(
        "database",
        exposure_store.trw_dir,
        TRWConfig(embeddings_enabled=False),
        max_results=2,
        _adapter_recall=lambda *a, **kw: entries,
    )
    # Only the returned rows are exposures; the discarded ones must stay untouched.
    expected = {"L-project-selected", "L-user-selected"}
    assert {entry["id"] for entry in result["learnings"]} == expected
    for tier, namespace in namespaces.items():
        for suffix in ("selected", "discarded"):
            lid = f"L-{tier}-{suffix}"
            entry = _get(exposure_store, lid, namespace)
            assert entry is not None
            assert entry["recall_count"] == int(lid in expected)
            assert entry["access_count"] == int(lid in expected)
            other_namespace = namespaces["user" if tier == "project" else "project"]
            assert _get(exposure_store, lid, other_namespace) is None
