from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from trw_mcp.scoring import _io_boundary as io_boundary


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
            ("skip", None, {"id": "skip"}, 1.0, 1, []),
        ]
    )
    assert written == [ok_path]
    assert updated == ["ok", "skip"]


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
