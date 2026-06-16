from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from structlog.testing import capture_logs

from trw_mcp.scoring import _io_boundary as io_boundary


def _all_log_values(captured: Sequence[Mapping[str, object]]) -> str:
    """Flatten every captured log event's values into one searchable string."""
    return "\n".join(str(value) for event in captured for value in event.values())


@pytest.mark.unit
def test_yaml_index_helpers_build_cache_and_backfill(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    entries_dir = tmp_path / "entries"
    entries_dir.mkdir()
    good = entries_dir / "good.yaml"
    bad = entries_dir / "bad.yaml"
    good.write_text("id: L1\n", encoding="utf-8")
    bad.write_text("id: broken\n", encoding="utf-8")

    class FakeReader:
        def read_yaml(self, path: Path) -> dict[str, object]:
            if path == good:
                return {"id": "L1"}
            raise OSError("unreadable")

    monkeypatch.setattr("trw_mcp.state._helpers.iter_yaml_entry_files", lambda _: [good, bad])
    monkeypatch.setattr("trw_mcp.state.persistence.FileStateReader", FakeReader)

    io_boundary._reset_yaml_path_index()
    built = io_boundary._get_yaml_path_index(entries_dir)
    assert built == {"L1": good}

    extra = entries_dir / "extra.yaml"
    io_boundary._backfill_yaml_path_index("L2", extra)
    cached = io_boundary._get_yaml_path_index(entries_dir)
    assert cached["L2"] == extra
    assert io_boundary._safe_mtime(entries_dir / "missing.yaml") is None


@pytest.mark.unit
def test_resolve_scoring_config_prefers_patched_correlation_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    original = io_boundary.sys.modules.get("trw_mcp.scoring._correlation")
    monkeypatch.setitem(
        io_boundary.sys.modules,
        "trw_mcp.scoring._correlation",
        SimpleNamespace(get_config=lambda: SimpleNamespace(runs_root=".trw/custom-runs")),
    )
    try:
        config = io_boundary._resolve_scoring_config()
    finally:
        if original is None:
            io_boundary.sys.modules.pop("trw_mcp.scoring._correlation", None)
        else:
            io_boundary.sys.modules["trw_mcp.scoring._correlation"] = original

    assert config.runs_root == ".trw/custom-runs"


@pytest.mark.unit
def test_read_recent_session_records_and_find_session_start_ts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trw_dir = tmp_path / ".trw"
    events_dir = tmp_path / ".trw" / "runs" / "task-a" / "run-1" / "meta"
    events_dir.mkdir(parents=True)
    events_path = events_dir / "events.jsonl"
    events_path.write_text(
        "\n".join(
            [
                "not-json",
                json.dumps({"event": "other", "ts": "2026-04-13T10:00:00"}),
                json.dumps({"event": "session_start", "ts": "bad-ts"}),
                json.dumps({"event": "session_start", "ts": "2026-04-13T11:30:00"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(io_boundary, "_resolve_scoring_config", lambda: SimpleNamespace(runs_root=".trw/runs"))

    records = io_boundary._read_recent_session_records(events_path)
    assert len(records) == 3
    assert io_boundary._read_recent_session_records(events_dir / "missing.jsonl") == []

    result = io_boundary._find_session_start_ts(trw_dir)
    assert result == datetime(2026, 4, 13, 11, 30, 0, tzinfo=timezone.utc)


@pytest.mark.unit
def test_default_lookup_entry_uses_sqlite_yaml_and_scan_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entries_dir = tmp_path / "entries"
    entries_dir.mkdir()
    yaml_path = entries_dir / "entry.yaml"
    yaml_path.write_text("id: L1\n", encoding="utf-8")

    monkeypatch.setattr(io_boundary, "_get_yaml_path_index", lambda _: {"L1": yaml_path, "L2": yaml_path})
    monkeypatch.setattr(
        "trw_mcp.state.memory_adapter.find_entry_by_id", lambda _trw_dir, lid: {"id": lid} if lid == "L1" else None
    )

    sqlite_path, sqlite_data = io_boundary._default_lookup_entry("L1", tmp_path / ".trw", entries_dir)
    assert sqlite_path == yaml_path
    assert sqlite_data == {"id": "L1"}

    class FakeReader:
        def read_yaml(self, path: Path) -> dict[str, object]:
            return {"id": "L2", "path": str(path)}

    monkeypatch.setattr("trw_mcp.state.persistence.FileStateReader", FakeReader)
    yaml_only_path, yaml_only_data = io_boundary._default_lookup_entry("L2", tmp_path / ".trw", entries_dir)
    assert yaml_only_path == yaml_path
    assert yaml_only_data == {"id": "L2", "path": str(yaml_path)}

    monkeypatch.setattr(io_boundary, "_get_yaml_path_index", lambda _: {})
    monkeypatch.setattr(
        "trw_mcp.state.analytics.find_entry_by_id", lambda _entries_dir, _lid: (yaml_path, {"id": "L3"})
    )
    fallback_path, fallback_data = io_boundary._default_lookup_entry("L3", tmp_path / ".trw", entries_dir)
    assert fallback_path == yaml_path
    assert fallback_data == {"id": "L3"}


@pytest.mark.unit
def test_sqlite_sync_and_yaml_write_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeBackend:
        def __init__(self) -> None:
            self.updated: list[str] = []

        class DummyTransaction:
            def __enter__(self) -> None:
                pass

            def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
                pass

        def transaction(self) -> DummyTransaction:
            return self.DummyTransaction()

        def update(
            self,
            lid: str,
            *,
            q_value: float,
            q_observations: int,
            outcome_history: list[str],
        ) -> None:
            if lid == "bad":
                raise RuntimeError("boom")
            self.updated.append(f"{lid}:{q_value}:{q_observations}:{len(outcome_history)}")

    backend = FakeBackend()
    monkeypatch.setattr("trw_mcp.state.memory_adapter.get_backend", lambda _trw_dir: backend)

    io_boundary._sync_to_sqlite("good", 1.23456, 4, ["ok"], tmp_path / ".trw")
    io_boundary._batch_sync_to_sqlite(
        [
            ("good", None, {}, 2.5, 5, ["ok"]),
            ("bad", None, {}, 3.0, 6, ["fail"]),
        ],
        tmp_path / ".trw",
    )
    assert backend.updated == ["good:1.2346:4:1", "good:2.5:5:1"]

    written: list[Path] = []

    class FakeWriter:
        def write_yaml(self, path: Path, data: dict[str, object]) -> None:
            if path.name == "bad.yaml":
                raise OSError("cannot write")
            written.append(path)

    monkeypatch.setattr("trw_mcp.state.persistence.FileStateWriter", FakeWriter)
    ok_path = tmp_path / "ok.yaml"
    bad_path = tmp_path / "bad.yaml"
    updated = io_boundary._write_pending_entries(
        [
            ("ok", ok_path, {"id": "ok"}, 1.0, 1, []),
            ("bad", bad_path, {"id": "bad"}, 1.0, 1, []),
            # entry_path=None means SQLite-only — no YAML write occurs, so
            # "skip" must NOT appear in updated_ids (was a bug before fix).
            ("skip", None, {"id": "skip"}, 1.0, 1, []),
        ]
    )
    assert written == [ok_path]
    # Regression: only "ok" was written successfully; "bad" failed the YAML
    # write; "skip" had no YAML path and must NOT be falsely reported as
    # written (PRD truthfulness invariant: never claim a write that did not happen).
    assert updated == ["ok"]


@pytest.mark.unit
def test_load_entries_and_recall_tracking_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    entries_dir = tmp_path / "entries"
    entries_dir.mkdir()
    good = entries_dir / "good.yaml"
    bad = entries_dir / "bad.yaml"
    good.write_text("id: good\n", encoding="utf-8")
    bad.write_text("id: bad\n", encoding="utf-8")

    class FakeReader:
        def read_yaml(self, path: Path) -> dict[str, object]:
            if path == bad:
                raise OSError("broken")
            return {"id": path.stem}

    monkeypatch.setattr("trw_mcp.state._helpers.iter_yaml_entry_files", lambda _: [good, bad])
    monkeypatch.setattr("trw_mcp.state.persistence.FileStateReader", FakeReader)
    assert list(io_boundary._load_entries_from_dir(entries_dir)) == [{"id": "good"}]

    receipt_path = tmp_path / "recall_tracking.jsonl"
    receipt_path.write_text(
        "\n".join(
            [
                json.dumps({"id": 1}),
                "bad-json",
                json.dumps(["not-a-dict"]),
                json.dumps({"id": 2}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert io_boundary._read_recall_tracking_jsonl(receipt_path) == [{"id": 1}, {"id": 2}]
    assert io_boundary._read_recall_tracking_jsonl(tmp_path / "missing.jsonl") == []

    large_path = tmp_path / "large.jsonl"
    lines = [f"line-{i}-{'x' * 16}" for i in range(6000)]
    large_path.write_text("\n".join(lines), encoding="utf-8")
    assert io_boundary._tail_lines(large_path, 3) == lines[-3:]


@pytest.mark.unit
def test_read_recall_tracking_skips_malformed_rows_without_leaking(tmp_path: Path) -> None:
    """Corrupt JSON and scalar/list rows are skipped with structural-only signals."""
    receipt_path = tmp_path / "recall_tracking.jsonl"
    receipt_path.write_text(
        "\n".join(
            [
                json.dumps({"id": 1, "query": "kept-row"}),
                '{"query": "LEAK_QUERY_BAD", ',  # malformed JSON
                json.dumps("LEAK_OUTCOME_SCALAR"),  # valid JSON, non-object scalar
                json.dumps(["LEAK_LIST_ITEM"]),  # valid JSON, non-object list
                json.dumps({"id": 2}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with capture_logs() as captured:
        records = io_boundary._read_recall_tracking_jsonl(receipt_path)

    # Valid dict rows survive; non-object/malformed rows are dropped.
    assert records == [{"id": 1, "query": "kept-row"}, {"id": 2}]

    skip_events = [e for e in captured if e.get("event") == "recall_tracking_row_skipped"]
    assert len(skip_events) == 3
    error_classes = sorted(str(e["error_class"]) for e in skip_events)
    assert error_classes == ["JSONDecodeError", "non_object:list", "non_object:str"]
    for event in skip_events:
        assert event["path"] == str(receipt_path)
        assert isinstance(event["tail_index"], int)

    # No sensitive sentinel from a skipped row may appear anywhere in the logs.
    haystack = _all_log_values(captured)
    for sentinel in ("LEAK_QUERY_BAD", "LEAK_OUTCOME_SCALAR", "LEAK_LIST_ITEM"):
        assert sentinel not in haystack


@pytest.mark.unit
def test_read_recent_session_records_isolates_non_utf8_row(tmp_path: Path) -> None:
    """A single non-UTF-8 row is skipped without losing adjacent valid rows."""
    events_path = tmp_path / "events.jsonl"
    good_first = json.dumps({"event": "run_init", "ts": "2026-04-13T10:00:00"}).encode("utf-8")
    good_last = json.dumps({"event": "session_start", "ts": "2026-04-13T11:00:00"}).encode("utf-8")
    # Raw non-UTF-8 bytes between two valid rows would abort a text-mode read.
    events_path.write_bytes(good_first + b"\n" + b'{"event": "\xff\xfe garbage"}' + b"\n" + good_last + b"\n")

    records = io_boundary._read_recent_session_records(events_path)

    assert [r.get("event") for r in records] == ["run_init", "session_start"]


@pytest.mark.unit
def test_tail_lines_small_file_decodes_non_utf8_without_raising(tmp_path: Path) -> None:
    """Small-file tail reads are byte-oriented: non-UTF-8 rows decode, not raise."""
    path = tmp_path / "small.jsonl"
    path.write_bytes(b"alpha\n\xff\xfe\nbeta\n")

    lines = io_boundary._tail_lines(path, 10)

    assert lines[0] == "alpha"
    assert lines[-1] == "beta"
    # The non-UTF-8 row decoded to replacement chars instead of aborting.
    assert any("�" in ln for ln in lines)


@pytest.mark.unit
def test_tail_lines_small_file_preserves_exact_max_lines_with_trailing_newline(tmp_path: Path) -> None:
    """Trailing newline split artifact must not evict a real tail record."""
    path = tmp_path / "small-tail.jsonl"
    path.write_bytes(b"one\ntwo\nthree\n")

    assert io_boundary._tail_lines(path, 3) == ["one", "two", "three"]


@pytest.mark.unit
def test_read_recall_tracking_isolates_non_utf8_tail_row(tmp_path: Path) -> None:
    """A non-UTF-8 tail row is isolated and skipped by JSON parsing; no leak."""
    receipt_path = tmp_path / "recall_tracking.jsonl"
    good_first = json.dumps({"id": 1, "query": "kept-first"}).encode("utf-8")
    good_last = json.dumps({"id": 2, "query": "kept-last"}).encode("utf-8")
    receipt_path.write_bytes(good_first + b"\n" + b"\xff\xfe\x00 LEAK_BYTES" + b"\n" + good_last + b"\n")

    with capture_logs() as captured:
        records = io_boundary._read_recall_tracking_jsonl(receipt_path)

    # Valid rows on both sides of the corrupt row survive.
    assert records == [{"id": 1, "query": "kept-first"}, {"id": 2, "query": "kept-last"}]

    skip_events = [e for e in captured if e.get("event") == "recall_tracking_row_skipped"]
    assert len(skip_events) == 1
    assert skip_events[0]["error_class"] == "JSONDecodeError"
    assert skip_events[0]["path"] == str(receipt_path)
    # The skip signal stays structural — no decoded row payload leaks.
    assert "LEAK_BYTES" not in _all_log_values(captured)


@pytest.mark.unit
def test_read_recall_tracking_read_failure_warns_and_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An OSError while tailing logs a safe warning and fails open to []."""
    receipt_path = tmp_path / "recall_tracking.jsonl"
    receipt_path.write_text(json.dumps({"id": 1}) + "\n", encoding="utf-8")

    def _boom(_path: Path, _max_lines: int) -> list[str]:
        raise OSError("disk gone SENSITIVE_ERR_BODY")

    monkeypatch.setattr(io_boundary, "_tail_lines", _boom)

    with capture_logs() as captured:
        records = io_boundary._read_recall_tracking_jsonl(receipt_path)

    assert records == []
    fail_events = [e for e in captured if e.get("event") == "recall_tracking_read_failed"]
    assert len(fail_events) == 1
    assert fail_events[0]["path"] == str(receipt_path)
    assert fail_events[0]["error_class"] == "OSError"
    # The OSError message body must not leak into observability.
    assert "SENSITIVE_ERR_BODY" not in _all_log_values(captured)
