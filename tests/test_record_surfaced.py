"""``memory_adapter.record_surfaced`` (PRD-CORE-280 slice e1/e3): the one call that
counts a recall's shown rows as accessed, and as surfaced at session start.
It goes through ``_store_selection.selected_store``, so a fake or daemon-backed
checkout can carry it.

Every behaviour-level case for the contract itself (dedupe, empty-list no-op,
the ``session_start`` flag, and fail-open logging when the store refuses)
lives here, in one place, rather than scattered across other files.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import structlog

from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.state import _store_selection
from trw_mcp.state.memory_adapter import record_surfaced, store_learning


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".trw"
    d.mkdir()
    (d / "learnings" / "entries").mkdir(parents=True)
    return d


class TestRecordSurfacedDedupeAndNoOp:
    def test_duplicate_ids_count_once(self, trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
        store_learning(trw_dir, "L-dup1", "s", "d")
        record_surfaced(trw_dir, ["L-dup1", "L-dup1", "L-dup1"])

        calls = [c for c in fake_memory_store.calls if c[0] == "record_surfaced"]
        assert len(calls) == 1
        ids, _session_start = calls[0][1]
        assert ids == ("L-dup1",), "duplicate ids in one call must be deduped before reaching the store"

        row = next(e for (_ns, eid), e in fake_memory_store.rows.items() if eid == "L-dup1")
        assert row.access_count == 1
        assert row.recall_count == 1

    def test_empty_list_is_a_no_op(self, trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
        record_surfaced(trw_dir, [])
        assert fake_memory_store.calls == [], "an empty id list must never reach the store"

    def test_falsy_ids_are_dropped_not_forwarded(self, trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
        store_learning(trw_dir, "L-real", "s", "d")
        record_surfaced(trw_dir, ["", "L-real", ""])

        calls = [c for c in fake_memory_store.calls if c[0] == "record_surfaced"]
        assert len(calls) == 1
        ids, _session_start = calls[0][1]
        assert ids == ("L-real",)

    def test_missing_id_is_silently_skipped(self, trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
        """An id the store does not hold is forwarded (and dropped by the store), never an error."""
        store_learning(trw_dir, "L-known", "s", "d")
        record_surfaced(trw_dir, ["L-known", "L-unknown"])

        row = next(e for (_ns, eid), e in fake_memory_store.rows.items() if eid == "L-known")
        assert row.access_count == 1
        assert not any(eid == "L-unknown" for (_ns, eid) in fake_memory_store.rows)

    def test_double_call_increments_twice(self, trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
        store_learning(trw_dir, "L-di1", "s", "d")
        record_surfaced(trw_dir, ["L-di1"])
        record_surfaced(trw_dir, ["L-di1"])

        row = next(e for (_ns, eid), e in fake_memory_store.rows.items() if eid == "L-di1")
        assert row.access_count == 2
        assert row.recall_count == 2


class TestRecordSurfacedSessionStart:
    def test_session_start_flag_bumps_session_count_once(
        self, trw_dir: Path, fake_memory_store: FakeMemoryStore
    ) -> None:
        store_learning(trw_dir, "L-sess1", "s", "d")
        record_surfaced(trw_dir, ["L-sess1"], session_start=True)

        row = next(e for (_ns, eid), e in fake_memory_store.rows.items() if eid == "L-sess1")
        assert row.session_count == 1
        assert row.access_count == 1
        assert row.recall_count == 1

    def test_default_call_never_bumps_session_count(self, trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
        store_learning(trw_dir, "L-sess2", "s", "d")
        record_surfaced(trw_dir, ["L-sess2"])

        row = next(e for (_ns, eid), e in fake_memory_store.rows.items() if eid == "L-sess2")
        assert row.session_count == 0


class TestRecordSurfacedFailsOpen:
    def test_store_unavailable_is_logged_not_raised(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(_trw_dir: Path) -> tuple[FakeMemoryStore, str]:
            raise _store_selection.StoreUnavailableError("no grant for this checkout")

        monkeypatch.setattr(_store_selection, "selected_store", _boom)

        with structlog.testing.capture_logs() as logs:
            record_surfaced(trw_dir, ["L-whatever"])  # must not raise

        failures = [log for log in logs if log.get("event") == "access_tracking_failed"]
        assert len(failures) == 1
        assert failures[0]["entry_ids"] == ["L-whatever"]

    def test_runtime_error_from_the_store_is_also_fail_open(
        self, trw_dir: Path, fake_memory_store: FakeMemoryStore
    ) -> None:
        def _boom(_ids: list[str], *, session_start: bool = False) -> None:
            raise RuntimeError("store refused")

        fake_memory_store.record_surfaced = _boom  # type: ignore[method-assign]

        with structlog.testing.capture_logs() as logs:
            record_surfaced(trw_dir, ["L-whatever"])  # must not raise

        assert any(log.get("event") == "access_tracking_failed" for log in logs)


def test_reviewer_role_never_calls_record_surfaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PRD-SEC-015: ``execute_recall`` gates the whole side effect on the reviewer role."""
    from unittest.mock import patch

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools._recall_impl import execute_recall

    monkeypatch.setattr("trw_mcp.state._surface_role.reviewer_role_active", lambda: True)
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()

    recorded = {"called": False}

    def _spy_record_surfaced(*_a: object, **_k: object) -> None:
        recorded["called"] = True

    with (
        patch("trw_mcp.tools._recall_impl.build_recall_context", return_value=None),
        patch("trw_mcp.tools._recall_impl._augment_with_remote", side_effect=lambda _q, m: (list(m), None)),
        patch("trw_mcp.state.memory_adapter.record_surfaced", side_effect=_spy_record_surfaced),
    ):
        result = execute_recall(
            "shared query",
            trw_dir,
            TRWConfig(),
            _adapter_recall=lambda _dir, **_kw: [{"id": "L-x", "summary": "hit", "impact": 0.5}],
            _rank_by_utility=lambda matches, *_a, **_k: list(matches),
        )

    assert result["learnings"], "sanity: the reviewer still gets results back"
    assert recorded["called"] is False, "reviewer role must never write access-tracking telemetry"


def test_a_shared_row_never_counts_the_local_row_sharing_its_id(tmp_path: Path) -> None:
    """A remote result whose id collides with a local row was shown; the local row was not."""
    from unittest.mock import patch

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools._recall_impl import execute_recall

    local = {"id": "L-local1", "summary": "local hit", "impact": 0.5}
    shared = {"id": "L-collide", "summary": "[shared] remote hit", "impact": 0.5, "source": "shared"}
    recorded: list[list[str]] = []

    with (
        patch("trw_mcp.tools._recall_impl.build_recall_context", return_value=None),
        patch("trw_mcp.tools._recall_impl._augment_with_remote", side_effect=lambda _q, m: ([*m, shared], None)),
        patch("trw_mcp.state.memory_adapter.record_surfaced", side_effect=lambda _d, ids, **_k: recorded.append(ids)),
    ):
        result = execute_recall(
            "hit",
            tmp_path / ".trw",
            TRWConfig(),
            _adapter_recall=lambda _dir, **_kw: [local],
            _rank_by_utility=lambda matches, *_a, **_k: list(matches),
        )

    assert {row["id"] for row in result["learnings"]} == {"L-local1", "L-collide"}  # type: ignore[index]
    assert recorded == [["L-local1"]]
