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
    # Genuinely unreadable, not merely unreadable-by-a-fake-reader. The index no
    # longer composes the document to find an id -- it reads the bytes and scans
    # for a top-level `id:` -- so simulating failure at the reader alone stopped
    # exercising anything: a file containing the literal text `id: broken` is
    # perfectly readable and would be indexed, which is correct behaviour and
    # made the old assertion fail for the right reason. A directory in a file's
    # place raises OSError in BOTH the byte read and the fallback parse, which is
    # what "unreadable entry" actually means.
    bad.mkdir()

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
def test_read_recent_session_records_isolates_non_utf8_row(tmp_path: Path) -> None:
    """A single non-UTF-8 row is skipped without losing adjacent valid rows."""
    events_path = tmp_path / "events.jsonl"
    good_first = json.dumps({"event": "run_init", "ts": "2026-04-13T10:00:00"}).encode("utf-8")
    good_last = json.dumps({"event": "session_start", "ts": "2026-04-13T11:00:00"}).encode("utf-8")
    # Raw non-UTF-8 bytes between two valid rows would abort a text-mode read.
    events_path.write_bytes(good_first + b"\n" + b'{"event": "\xff\xfe garbage"}' + b"\n" + good_last + b"\n")

    records = io_boundary._read_recent_session_records(events_path)

    assert [r.get("event") for r in records] == ["run_init", "session_start"]
