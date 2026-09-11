"""Recall exposure must describe returned records, not prefetched candidates.

Candidate acquisition is controlled; production selection, access counters and
the durable recall receipt writer execute against an isolated SQLite store.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from trw_memory.models.memory import MemoryEntry

from trw_mcp.models.config import TRWConfig
from trw_mcp.scoring._recall_window import correlate_recalls
from trw_mcp.state.memory_adapter import get_backend
from trw_mcp.tools import _recall_impl


@pytest.fixture
def exposure_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TRW_EMBEDDINGS_ENABLED", "false")
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    monkeypatch.setattr(_recall_impl, "build_recall_context", lambda *a, **kw: None)
    monkeypatch.setattr(_recall_impl, "_augment_with_remote", lambda query, rows: (rows, None))
    monkeypatch.setattr("trw_mcp.state.recall_tracking.resolve_trw_dir", lambda: trw_dir)
    return trw_dir


def _candidates(trw_dir: Path, *, duplicate: bool = False) -> list[dict[str, object]]:
    backend = get_backend(trw_dir)
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
        backend.store(MemoryEntry(id=lid, content=summary, namespace="default"))
        entries.append({"id": lid, "summary": summary, "impact": 0.8 - index * 0.2})
    return entries


def _assert_exposure(trw_dir: Path, returned: list[dict[str, object]]) -> None:
    expected = [str(row["id"]) for row in returned]
    receipts = trw_dir / "logs" / "recall_tracking.jsonl"
    tracked = (
        [json.loads(line)["learning_id"] for line in receipts.read_text().splitlines()] if receipts.exists() else []
    )
    assert tracked == expected
    assert {lid for lid, _discount in correlate_recalls(trw_dir, 5, scope="window")} == set(expected)
    backend = get_backend(trw_dir)
    for index in range(3):
        lid = f"L-exposure{index}"
        row = backend.get(lid, namespace="default")
        assert row is not None
        assert row.recall_count == int(lid in expected)
        assert row.access_count == int(lid in expected)


@pytest.mark.parametrize("mode", ["full", "compact", "ultra", "budget", "dedup", "empty"])
def test_only_returned_entries_receive_exposure(exposure_store: Path, mode: str) -> None:
    entries = _candidates(exposure_store, duplicate=mode == "dedup")
    if mode == "empty":
        entries = []
    result = _recall_impl.execute_recall(
        "database",
        exposure_store,
        TRWConfig(embeddings_enabled=False),
        max_results=10 if mode in {"budget", "dedup"} else 1,
        compact=mode != "full",
        ultra_compact=mode == "ultra",
        token_budget=40 if mode == "budget" else 4000,
        _adapter_recall=lambda *a, **kw: entries,
        _search_patterns=lambda *a, **kw: [],
        _collect_context=lambda *a, **kw: {},
    )
    returned = result["learnings"]
    assert len(returned) < 3
    if mode != "empty":
        assert returned
    _assert_exposure(exposure_store, returned)


def test_remote_selection_not_local_prefetch_drives_tracking(
    exposure_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = _candidates(exposure_store)
    remote = {"id": "L-remote", "summary": "Database migration", "impact": 1.0}
    monkeypatch.setattr(_recall_impl, "_augment_with_remote", lambda query, rows: ([remote], None))
    result = _recall_impl.execute_recall(
        "database",
        exposure_store,
        TRWConfig(embeddings_enabled=False),
        max_results=1,
        compact=True,
        _adapter_recall=lambda *a, **kw: entries,
        _search_patterns=lambda *a, **kw: [],
        _collect_context=lambda *a, **kw: {},
    )
    assert result["learnings"][0]["id"] == "L-remote"
    _assert_exposure(exposure_store, result["learnings"])


def test_reviewer_does_not_write_exposure(exposure_store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entries = _candidates(exposure_store)
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    result = _recall_impl.execute_recall(
        "database",
        exposure_store,
        TRWConfig(embeddings_enabled=False),
        max_results=1,
        compact=True,
        _adapter_recall=lambda *a, **kw: entries,
        _search_patterns=lambda *a, **kw: [],
        _collect_context=lambda *a, **kw: {},
    )
    assert len(result["learnings"]) == 1
    _assert_exposure(exposure_store, [])

    surface_path = exposure_store / "logs" / "surface_tracking.jsonl"
    assert not surface_path.exists() or not surface_path.read_text().strip()


def test_real_acquisition_and_receipts_agree(exposure_store: Path) -> None:
    """No acquisition or attribution doubles: real SQLite search to receipts."""
    _candidates(exposure_store)
    result = _recall_impl.execute_recall(
        "database",
        exposure_store,
        TRWConfig(embeddings_enabled=False),
        max_results=1,
        compact=True,
        _search_patterns=lambda *a, **kw: [],
        _collect_context=lambda *a, **kw: {},
    )
    assert len(result["learnings"]) == 1
    _assert_exposure(exposure_store, result["learnings"])


@pytest.mark.parametrize("failure_stage", ["verification", "context"])
def test_failed_response_preparation_does_not_record_exposure(
    exposure_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    entries = _candidates(exposure_store)

    def fail_verification(*args: object, **kwargs: object) -> list[dict[str, object]]:
        raise RuntimeError("verification aborted response")

    if failure_stage == "verification":
        monkeypatch.setattr(_recall_impl, "_verify_assertions", fail_verification)
    with pytest.raises(RuntimeError, match="verification aborted"):
        _recall_impl.execute_recall(
            "database",
            exposure_store,
            TRWConfig(embeddings_enabled=False),
            max_results=1,
            compact=False,
            _adapter_recall=lambda *a, **kw: entries,
            _search_patterns=lambda *a, **kw: [],
            _collect_context=fail_verification if failure_stage == "context" else lambda *a, **kw: {},
        )
    _assert_exposure(exposure_store, [])

    for filename in ("surface_tracking.jsonl", "propensity.jsonl"):
        log_path = exposure_store / "logs" / filename
        assert not log_path.exists() or not log_path.read_text().strip()


def test_selected_records_increment_only_their_real_owning_stores(
    exposure_store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.state._user_tier import get_user_backend

    monkeypatch.setenv("TRW_USER_DIR", str(exposure_store.parent / "isolated-user"))
    stores = {"project": get_backend(exposure_store), "user": get_user_backend()}
    entries = []
    for tier, backend in stores.items():
        for selected in (True, False):
            lid = f"L-{tier}-{'selected' if selected else 'discarded'}"
            summary = {
                ("project", True): "Database migration verifies schema constraints",
                ("project", False): "Filesystem backup retention uses expiration dates",
                ("user", True): "Database connection pooling requires idle timeout limits",
                ("user", False): "Filesystem credentials rotate using deployment secrets",
            }[tier, selected]
            backend.store(MemoryEntry(id=lid, content=summary, namespace="default"))
            entries.append({"id": lid, "summary": summary, "impact": 0.9 if selected else 0.1})
    result = _recall_impl.execute_recall(
        "database",
        exposure_store,
        TRWConfig(embeddings_enabled=False),
        max_results=2,
        compact=True,
        _adapter_recall=lambda *a, **kw: entries,
        _search_patterns=lambda *a, **kw: [],
        _collect_context=lambda *a, **kw: {},
    )
    # Selected rows match the query; utility must not override query relevance.
    expected = {"L-project-selected", "L-user-selected"}
    assert {entry["id"] for entry in result["learnings"]} == expected
    for tier, backend in stores.items():
        for suffix in ("selected", "discarded"):
            lid = f"L-{tier}-{suffix}"
            entry = backend.get(lid, namespace="default")
            assert entry is not None
            assert entry.recall_count == int(lid in expected)
            assert entry.access_count == int(lid in expected)
            other = stores["user" if tier == "project" else "project"]
            assert other.get(lid, namespace="default") is None


def test_injected_access_adapter_retains_two_argument_contract(exposure_store: Path) -> None:
    entries = _candidates(exposure_store)
    calls: list[tuple[Path, list[str]]] = []

    def legacy_access(path: Path, ids: list[str]) -> None:
        calls.append((path, ids))

    result = _recall_impl.execute_recall(
        "database",
        exposure_store,
        TRWConfig(embeddings_enabled=False),
        max_results=1,
        compact=True,
        _adapter_recall=lambda *a, **kw: entries,
        _adapter_update_access=legacy_access,
        _search_patterns=lambda *a, **kw: [],
        _collect_context=lambda *a, **kw: {},
    )
    assert calls == [(exposure_store, [entry["id"] for entry in result["learnings"]])]
    assert len(calls[0][1]) == 1
