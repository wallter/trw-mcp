"""Focused writer and logger edge-case tests for state/persistence.py."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from trw_mcp.exceptions import StateError
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter


class TestAppendJsonlEdgeCases:
    """Edge cases for FileStateWriter.append_jsonl."""

    def test_multiple_appends_accumulate(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Successive append_jsonl calls accumulate records in order."""
        jsonl_file = tmp_path / "events.jsonl"

        writer.append_jsonl(jsonl_file, {"seq": 1, "msg": "first"})
        writer.append_jsonl(jsonl_file, {"seq": 2, "msg": "second"})
        writer.append_jsonl(jsonl_file, {"seq": 3, "msg": "third"})

        records = reader.read_jsonl(jsonl_file)
        assert len(records) == 3
        assert [r["seq"] for r in records] == [1, 2, 3]

    def test_append_jsonl_creates_parent_dirs(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """append_jsonl creates missing parent directories."""
        deep_path = tmp_path / "logs" / "sub" / "events.jsonl"
        assert not deep_path.parent.exists()

        writer.append_jsonl(deep_path, {"event": "test"})
        assert deep_path.exists()

        content = deep_path.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert record["event"] == "test"

    def test_append_jsonl_non_serializable_raises_state_error(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Non-serializable value (set) in record raises StateError."""
        jsonl_file = tmp_path / "bad.jsonl"

        with pytest.raises(StateError, match="Failed to append JSONL"):
            writer.append_jsonl(jsonl_file, {"data": {1, 2, 3}})

    def test_append_jsonl_date_value_serialized(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """date objects in record values are serialized via json_serializer."""
        jsonl_file = tmp_path / "dates.jsonl"
        d = date(2026, 3, 11)
        writer.append_jsonl(jsonl_file, {"day": d})

        line = jsonl_file.read_text(encoding="utf-8").strip()
        record = json.loads(line)
        assert record["day"] == "2026-03-11"

    def test_append_jsonl_preserves_existing_content(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Appending does not corrupt previously written records."""
        jsonl_file = tmp_path / "log.jsonl"

        writer.append_jsonl(jsonl_file, {"id": "a"})
        first_content = jsonl_file.read_text(encoding="utf-8")

        writer.append_jsonl(jsonl_file, {"id": "b"})
        full_content = jsonl_file.read_text(encoding="utf-8")

        assert full_content.startswith(first_content)

        records = reader.read_jsonl(jsonl_file)
        assert len(records) == 2
        assert records[0]["id"] == "a"
        assert records[1]["id"] == "b"


class TestWriteTextEdgeCases:
    """Edge cases for FileStateWriter.write_text."""

    def test_write_text_creates_parent_directories(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """write_text creates missing parent directories."""
        deep_path = tmp_path / "x" / "y" / "z" / "file.md"
        assert not deep_path.parent.exists()

        writer.write_text(deep_path, "# Hello\n")
        assert deep_path.exists()
        assert deep_path.read_text(encoding="utf-8") == "# Hello\n"

    def test_write_text_overwrites_atomically(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """write_text replaces file content completely."""
        text_file = tmp_path / "doc.txt"
        writer.write_text(text_file, "version one")
        writer.write_text(text_file, "version two")

        assert text_file.read_text(encoding="utf-8") == "version two"

    def test_write_text_empty_string(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """write_text with empty string creates an empty file."""
        text_file = tmp_path / "empty.txt"
        writer.write_text(text_file, "")

        assert text_file.exists()
        assert text_file.read_text(encoding="utf-8") == ""

    def test_write_text_unicode_content(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """write_text preserves unicode content."""
        text_file = tmp_path / "unicode.txt"
        content = "日本語テスト — ñ — ö — ∞"
        writer.write_text(text_file, content)

        assert text_file.read_text(encoding="utf-8") == content

    def test_write_text_multiline(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """write_text handles multiline content with mixed line endings."""
        text_file = tmp_path / "multi.txt"
        content = "line1\nline2\nline3\n"
        writer.write_text(text_file, content)

        assert text_file.read_text(encoding="utf-8") == content


class TestFileEventLoggerEdgeCases:
    """Edge cases for FileEventLogger."""

    def test_default_writer_created_when_none(self, tmp_path: Path) -> None:
        """FileEventLogger creates its own FileStateWriter when none provided."""
        logger = FileEventLogger()
        events_path = tmp_path / "events.jsonl"

        logger.log_event(events_path, "test_event", {"key": "val"})

        content = events_path.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert record["event"] == "test_event"
        assert record["key"] == "val"

    def test_event_has_iso_timestamp_with_timezone(self, tmp_path: Path, event_logger: FileEventLogger) -> None:
        """Event record contains ISO 8601 timestamp with UTC timezone."""
        events_path = tmp_path / "events.jsonl"
        event_logger.log_event(events_path, "ts_check", {})

        content = events_path.read_text(encoding="utf-8").strip()
        record = json.loads(content)

        ts = record["ts"]
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None

    def test_data_keys_merged_into_record(self, tmp_path: Path, event_logger: FileEventLogger) -> None:
        """Additional data keys are merged alongside ts and event."""
        events_path = tmp_path / "events.jsonl"
        event_logger.log_event(
            events_path,
            "merge_test",
            {"alpha": 1, "beta": "two", "gamma": True},
        )

        content = events_path.read_text(encoding="utf-8").strip()
        record = json.loads(content)

        assert record["event"] == "merge_test"
        assert record["alpha"] == 1
        assert record["beta"] == "two"
        assert record["gamma"] is True
        assert "ts" in record

    def test_multiple_events_logged_sequentially(
        self, tmp_path: Path, event_logger: FileEventLogger, reader: FileStateReader
    ) -> None:
        """Multiple log_event calls create separate JSONL records."""
        events_path = tmp_path / "events.jsonl"
        event_logger.log_event(events_path, "first", {"n": 1})
        event_logger.log_event(events_path, "second", {"n": 2})

        records = reader.read_jsonl(events_path)
        assert len(records) == 2
        assert records[0]["event"] == "first"
        assert records[1]["event"] == "second"
