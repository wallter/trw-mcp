"""Tests for PRD-INFRA-067 integrity-on-delivery wiring (C2, revised for C12 finding 3).

The probe used to open the checkout's own ``memory.db`` directly, so a
migrated checkout with no such file reported ``db_missing`` -> ``ok=True``
even though the real store (the daemon) might be unreachable. It now probes
the SELECTED store (``trw_mcp.state._store_selection.selected_store``) and
never opens a checkout db.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.state import _store_selection
from trw_mcp.state._store_selection import StoreUnavailableError
from trw_mcp.tools._deliver_integrity import check_memory_integrity_on_deliver

FAKE_NAMESPACE = "project:fake"


def _setup_run_dir(tmp_path: Path) -> Path:
    run = tmp_path / "runs" / "sprint-x" / "run-y"
    (run / "meta").mkdir(parents=True)
    (run / "meta" / "events.jsonl").write_text("")
    return run


# ---------------------------------------------------------------------------
# Healthy / unavailable paths
# ---------------------------------------------------------------------------


def test_integrity_ok_when_selected_store_is_healthy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeMemoryStore()
    monkeypatch.setattr(_store_selection, "selected_store", lambda _trw_dir: (store, FAKE_NAMESPACE))
    trw_dir = tmp_path / ".trw"

    result = check_memory_integrity_on_deliver(trw_dir)

    assert result["ok"] is True
    assert result["detail"].startswith("reachable; integrity not checked")
    assert result["namespace"] == FAKE_NAMESPACE
    assert result["checked_at"]  # non-empty ISO string
    assert ("health", FAKE_NAMESPACE) in store.calls


def test_integrity_not_measured_when_store_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreachable daemon or unpinned checkout must never read as healthy."""

    def _raise(_trw_dir: Path) -> tuple[object, str]:
        raise StoreUnavailableError("daemon unreachable")

    monkeypatch.setattr(_store_selection, "selected_store", _raise)
    trw_dir = tmp_path / ".trw"

    result = check_memory_integrity_on_deliver(trw_dir)

    assert result["ok"] is False
    assert result["detail"].startswith("not_measured")


def test_integrity_not_measured_when_health_call_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A store that resolves but fails its health probe is also 'not measured', not healthy."""

    class _BoomStore(FakeMemoryStore):
        def health(self, namespace: str) -> _store_selection.NamespaceHealth:  # type: ignore[override]
            raise RuntimeError("store boom")

    store = _BoomStore()
    monkeypatch.setattr(_store_selection, "selected_store", lambda _trw_dir: (store, FAKE_NAMESPACE))
    trw_dir = tmp_path / ".trw"

    result = check_memory_integrity_on_deliver(trw_dir)

    assert result["ok"] is False
    assert "not_measured" in result["detail"]


def test_integrity_probe_never_opens_a_checkout_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The retired checkout ``memory.db`` must never be opened by this probe (C12 finding 3)."""
    import sqlite3

    store = FakeMemoryStore()
    monkeypatch.setattr(_store_selection, "selected_store", lambda _trw_dir: (store, FAKE_NAMESPACE))

    trw_dir = tmp_path / ".trw"
    mem_dir = trw_dir / "memory"
    mem_dir.mkdir(parents=True)
    db_path = mem_dir / "memory.db"
    db_bytes = b"sqlite-db-fixture-bytes"
    db_path.write_bytes(db_bytes)

    def _boom(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        raise AssertionError("sqlite3.connect must not be called against the checkout db")

    monkeypatch.setattr(sqlite3, "connect", _boom)

    result = check_memory_integrity_on_deliver(trw_dir)

    assert result["ok"] is True
    assert db_path.read_bytes() == db_bytes


# ---------------------------------------------------------------------------
# Event-log emission
# ---------------------------------------------------------------------------


def test_event_emitted_when_run_dir_provided(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeMemoryStore()
    monkeypatch.setattr(_store_selection, "selected_store", lambda _trw_dir: (store, FAKE_NAMESPACE))
    trw_dir = tmp_path / ".trw"
    run_dir = _setup_run_dir(tmp_path)

    check_memory_integrity_on_deliver(trw_dir, run_dir)

    events_path = run_dir / "meta" / "events.jsonl"
    lines = [ln for ln in events_path.read_text().splitlines() if ln.strip()]
    assert len(lines) >= 1
    events = [json.loads(ln) for ln in lines]
    names = [e.get("event") for e in events]
    assert "db_integrity_check_on_deliver" in names


def test_no_event_when_no_run_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeMemoryStore()
    monkeypatch.setattr(_store_selection, "selected_store", lambda _trw_dir: (store, FAKE_NAMESPACE))
    trw_dir = tmp_path / ".trw"

    # No run_dir passed — result still returned, but no events.jsonl file.
    result = check_memory_integrity_on_deliver(trw_dir, None)
    assert result["ok"] is True


def test_failure_still_returns_dict_with_stable_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Observability: probe failure MUST NEVER raise; always return a dict."""

    def _raise(_trw_dir: Path) -> tuple[object, str]:
        raise StoreUnavailableError("daemon unreachable")

    monkeypatch.setattr(_store_selection, "selected_store", _raise)
    trw_dir = tmp_path / ".trw"
    run_dir = _setup_run_dir(tmp_path)

    result = check_memory_integrity_on_deliver(trw_dir, run_dir)

    assert set(result.keys()) == {"ok", "detail", "namespace", "checked_at"}
    assert result["ok"] is False
