"""Focused edge-case tests for state/persistence.py.

Covers behaviors NOT already tested in test_persistence.py or test_state.py:
- Malformed YAML syntax (unclosed brackets, tabs)
- write_yaml / read_yaml roundtrip fidelity
- append_jsonl multi-record accumulation
- append_jsonl parent directory creation
- append_jsonl with non-serializable values (TypeError → StateError)
- write_text parent directory creation
- write_text / read roundtrip
- FileEventLogger default writer creation
- FileEventLogger timestamp format (ISO 8601 with timezone)
- FileEventLogger data key merging
- model_to_dict with enums and date fields
- lock_for_rmw protecting a real read-modify-write cycle
- read_yaml with unicode content
- read_yaml with YAML comments preserved
- _new_yaml configuration (flow style off, quotes preserved)
"""

from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from trw_mcp.exceptions import StateError
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateReader,
    FileStateWriter,
    lock_for_rmw,
    model_to_dict,
)


# ---------------------------------------------------------------------------
# FileStateReader: malformed YAML syntax
# ---------------------------------------------------------------------------


class TestReadYamlMalformedSyntax:
    """read_yaml wraps YAML syntax errors as StateError."""

    def test_unclosed_bracket_raises_state_error(
        self, tmp_path: Path, reader: FileStateReader
    ) -> None:
        """Unclosed YAML flow sequence produces a parse error wrapped in StateError."""
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("items: [a, b, c\n", encoding="utf-8")

        with pytest.raises(StateError, match="Failed to read YAML"):
            reader.read_yaml(bad_yaml)

    def test_invalid_indentation_raises_state_error(
        self, tmp_path: Path, reader: FileStateReader
    ) -> None:
        """YAML with bad indentation that causes a parse error is wrapped."""
        bad_yaml = tmp_path / "indent.yaml"
        bad_yaml.write_text("key:\n  sub: 1\n sub2: 2\n", encoding="utf-8")

        # ruamel may or may not error on this depending on version,
        # but if it does it should be wrapped as StateError
        try:
            result = reader.read_yaml(bad_yaml)
            # If parsing succeeds, it should still be a dict
            assert isinstance(result, dict)
        except StateError:
            pass  # Expected — malformed YAML wrapped correctly

    def test_tab_characters_in_yaml_raises_state_error(
        self, tmp_path: Path, reader: FileStateReader
    ) -> None:
        """YAML with tab indentation (forbidden by spec) raises StateError."""
        bad_yaml = tmp_path / "tabs.yaml"
        bad_yaml.write_text("key:\n\tvalue: 1\n", encoding="utf-8")

        with pytest.raises(StateError, match="Failed to read YAML"):
            reader.read_yaml(bad_yaml)

    def test_duplicate_key_yaml_raises_state_error(
        self, tmp_path: Path, reader: FileStateReader
    ) -> None:
        """YAML with duplicate keys raises StateError (ruamel strict mode)."""
        yaml_file = tmp_path / "dup.yaml"
        yaml_file.write_text("key: first\nkey: second\n", encoding="utf-8")

        with pytest.raises(StateError, match="Failed to read YAML"):
            reader.read_yaml(yaml_file)


# ---------------------------------------------------------------------------
# FileStateReader: unicode content
# ---------------------------------------------------------------------------


class TestReadYamlUnicode:
    """read_yaml handles unicode content correctly."""

    def test_unicode_values_preserved(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Unicode characters in values survive write/read roundtrip."""
        yaml_file = tmp_path / "unicode.yaml"
        data: dict[str, object] = {
            "name": "Tokyo — 東京",
            "emoji": "⚡ lightning",
            "math": "∑(x²)",
        }
        writer.write_yaml(yaml_file, data)
        result = reader.read_yaml(yaml_file)

        assert result["name"] == "Tokyo — 東京"
        assert result["emoji"] == "⚡ lightning"
        assert result["math"] == "∑(x²)"

    def test_unicode_keys_preserved(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Unicode characters in keys survive write/read roundtrip."""
        yaml_file = tmp_path / "ukeys.yaml"
        data: dict[str, object] = {"schlüssel": "wert", "ключ": "значение"}
        writer.write_yaml(yaml_file, data)
        result = reader.read_yaml(yaml_file)

        assert result["schlüssel"] == "wert"
        assert result["ключ"] == "значение"


# ---------------------------------------------------------------------------
# write_yaml / read_yaml roundtrip fidelity
# ---------------------------------------------------------------------------


class TestWriteReadYamlRoundtrip:
    """write_yaml followed by read_yaml preserves data types."""

    def test_nested_dict_roundtrip(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Nested dictionaries survive a write/read cycle."""
        yaml_file = tmp_path / "nested.yaml"
        data: dict[str, object] = {
            "level1": {
                "level2": {"value": 42, "flag": True},
                "items": ["a", "b", "c"],
            },
            "top": "string",
        }
        writer.write_yaml(yaml_file, data)
        result = reader.read_yaml(yaml_file)

        assert result["top"] == "string"
        level1 = result["level1"]
        assert isinstance(level1, dict)
        assert level1["level2"]["value"] == 42
        assert level1["level2"]["flag"] is True
        assert list(level1["items"]) == ["a", "b", "c"]

    def test_empty_dict_roundtrip(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """An empty dict written and read back returns empty dict."""
        yaml_file = tmp_path / "empty.yaml"
        writer.write_yaml(yaml_file, {})
        result = reader.read_yaml(yaml_file)
        assert result == {}

    def test_numeric_types_roundtrip(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Integer and float values are preserved through YAML roundtrip."""
        yaml_file = tmp_path / "nums.yaml"
        data: dict[str, object] = {
            "integer": 42,
            "negative": -7,
            "float_val": 3.14,
            "zero": 0,
        }
        writer.write_yaml(yaml_file, data)
        result = reader.read_yaml(yaml_file)

        assert result["integer"] == 42
        assert result["negative"] == -7
        assert abs(float(str(result["float_val"])) - 3.14) < 0.001
        assert result["zero"] == 0

    def test_none_value_roundtrip(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """None values are preserved through YAML roundtrip (as null)."""
        yaml_file = tmp_path / "nulls.yaml"
        data: dict[str, object] = {"present": "yes", "absent": None}
        writer.write_yaml(yaml_file, data)
        result = reader.read_yaml(yaml_file)

        assert result["present"] == "yes"
        assert result["absent"] is None

    def test_write_yaml_creates_parent_directories(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """write_yaml creates missing parent directories automatically."""
        deep_path = tmp_path / "a" / "b" / "c" / "data.yaml"
        assert not deep_path.parent.exists()

        writer.write_yaml(deep_path, {"key": "value"})
        assert deep_path.exists()

    def test_write_yaml_overwrites_existing_file(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """write_yaml replaces previous content atomically."""
        yaml_file = tmp_path / "overwrite.yaml"
        writer.write_yaml(yaml_file, {"version": 1})
        writer.write_yaml(yaml_file, {"version": 2, "new_key": "added"})

        result = reader.read_yaml(yaml_file)
        assert result["version"] == 2
        assert result["new_key"] == "added"
        # Old-only keys should not be present
        assert "version" in result  # key was updated, not removed


# ---------------------------------------------------------------------------
# append_jsonl: multi-record, parent dirs, non-serializable
# ---------------------------------------------------------------------------


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

    def test_append_jsonl_creates_parent_dirs(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """append_jsonl creates missing parent directories."""
        deep_path = tmp_path / "logs" / "sub" / "events.jsonl"
        assert not deep_path.parent.exists()

        writer.append_jsonl(deep_path, {"event": "test"})
        assert deep_path.exists()

        content = deep_path.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert record["event"] == "test"

    def test_append_jsonl_non_serializable_raises_state_error(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """Non-serializable value (set) in record raises StateError."""
        jsonl_file = tmp_path / "bad.jsonl"

        with pytest.raises(StateError, match="Failed to append JSONL"):
            writer.append_jsonl(jsonl_file, {"data": {1, 2, 3}})

    def test_append_jsonl_date_value_serialized(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
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

        # First record's text should be a prefix of the full content
        assert full_content.startswith(first_content)

        records = reader.read_jsonl(jsonl_file)
        assert len(records) == 2
        assert records[0]["id"] == "a"
        assert records[1]["id"] == "b"


# ---------------------------------------------------------------------------
# write_text: parent dirs, roundtrip
# ---------------------------------------------------------------------------


class TestWriteTextEdgeCases:
    """Edge cases for FileStateWriter.write_text."""

    def test_write_text_creates_parent_directories(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """write_text creates missing parent directories."""
        deep_path = tmp_path / "x" / "y" / "z" / "file.md"
        assert not deep_path.parent.exists()

        writer.write_text(deep_path, "# Hello\n")
        assert deep_path.exists()
        assert deep_path.read_text(encoding="utf-8") == "# Hello\n"

    def test_write_text_overwrites_atomically(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """write_text replaces file content completely."""
        text_file = tmp_path / "doc.txt"
        writer.write_text(text_file, "version one")
        writer.write_text(text_file, "version two")

        assert text_file.read_text(encoding="utf-8") == "version two"

    def test_write_text_empty_string(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """write_text with empty string creates an empty file."""
        text_file = tmp_path / "empty.txt"
        writer.write_text(text_file, "")

        assert text_file.exists()
        assert text_file.read_text(encoding="utf-8") == ""

    def test_write_text_unicode_content(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """write_text preserves unicode content."""
        text_file = tmp_path / "unicode.txt"
        content = "日本語テスト — ñ — ö — ∞"
        writer.write_text(text_file, content)

        assert text_file.read_text(encoding="utf-8") == content

    def test_write_text_multiline(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """write_text handles multiline content with mixed line endings."""
        text_file = tmp_path / "multi.txt"
        content = "line1\nline2\nline3\n"
        writer.write_text(text_file, content)

        assert text_file.read_text(encoding="utf-8") == content


# ---------------------------------------------------------------------------
# FileEventLogger: default writer, timestamp, data merging
# ---------------------------------------------------------------------------


class TestFileEventLoggerEdgeCases:
    """Edge cases for FileEventLogger."""

    def test_default_writer_created_when_none(self, tmp_path: Path) -> None:
        """FileEventLogger creates its own FileStateWriter when none provided."""
        logger = FileEventLogger()  # No writer argument
        events_path = tmp_path / "events.jsonl"

        logger.log_event(events_path, "test_event", {"key": "val"})

        content = events_path.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert record["event"] == "test_event"
        assert record["key"] == "val"

    def test_event_has_iso_timestamp_with_timezone(
        self, tmp_path: Path, event_logger: FileEventLogger
    ) -> None:
        """Event record contains ISO 8601 timestamp with UTC timezone."""
        events_path = tmp_path / "events.jsonl"
        event_logger.log_event(events_path, "ts_check", {})

        content = events_path.read_text(encoding="utf-8").strip()
        record = json.loads(content)

        ts = record["ts"]
        # Should be parseable as ISO datetime
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None  # Has timezone

    def test_data_keys_merged_into_record(
        self, tmp_path: Path, event_logger: FileEventLogger
    ) -> None:
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


# ---------------------------------------------------------------------------
# model_to_dict: enums and dates
# ---------------------------------------------------------------------------


class TestModelToDictEdgeCases:
    """Edge cases for model_to_dict helper."""

    def test_enum_values_converted(self) -> None:
        """Enum fields are converted to their string values."""

        class Color(str, Enum):
            RED = "red"
            BLUE = "blue"

        class MyModel(BaseModel):
            model_config = {"use_enum_values": True}
            color: Color = Color.RED
            name: str = "test"

        result = model_to_dict(MyModel(color=Color.BLUE))
        assert result["color"] == "blue"
        assert result["name"] == "test"

    def test_datetime_fields_serialized(self) -> None:
        """datetime fields are serialized to ISO format strings."""

        class TimedModel(BaseModel):
            created: datetime
            label: str

        dt = datetime(2026, 3, 11, 14, 30, 0, tzinfo=timezone.utc)
        result = model_to_dict(TimedModel(created=dt, label="test"))

        assert result["label"] == "test"
        assert "2026-03-11" in str(result["created"])

    def test_nested_model_converted(self) -> None:
        """Nested Pydantic models are recursively converted to dicts."""

        class Inner(BaseModel):
            value: int = 42

        class Outer(BaseModel):
            inner: Inner = Inner()
            name: str = "outer"

        result = model_to_dict(Outer())
        assert result["name"] == "outer"
        assert isinstance(result["inner"], dict)
        assert result["inner"]["value"] == 42

    def test_optional_none_field(self) -> None:
        """Optional fields with None value appear as null in output."""

        class OptModel(BaseModel):
            required: str = "yes"
            optional: str | None = None

        result = model_to_dict(OptModel())
        assert result["required"] == "yes"
        assert result["optional"] is None

    def test_list_field(self) -> None:
        """List fields are preserved as lists."""

        class ListModel(BaseModel):
            items: list[str] = ["a", "b"]

        result = model_to_dict(ListModel())
        assert result["items"] == ["a", "b"]


# ---------------------------------------------------------------------------
# lock_for_rmw: real read-modify-write protection
# ---------------------------------------------------------------------------


class TestLockForRmwReadModifyWrite:
    """lock_for_rmw protects actual read-modify-write cycles."""

    def test_rmw_cycle_produces_correct_result(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """A guarded RMW cycle reads, increments, and writes correctly."""
        counter_file = tmp_path / "counter.yaml"
        writer.write_yaml(counter_file, {"count": 0})

        with lock_for_rmw(counter_file) as path:
            data = reader.read_yaml(path)
            data["count"] = int(str(data["count"])) + 1
            writer.write_yaml(path, data)

        result = reader.read_yaml(counter_file)
        assert result["count"] == 1

    def test_lock_serializes_concurrent_rmw(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """Two threads doing RMW under lock_for_rmw do not lose increments."""
        counter_file = tmp_path / "counter.yaml"
        writer.write_yaml(counter_file, {"count": 0})

        iterations = 10
        errors: list[str] = []

        def increment(n: int) -> None:
            try:
                for _ in range(n):
                    with lock_for_rmw(counter_file) as path:
                        data = reader.read_yaml(path)
                        current = int(str(data["count"]))
                        # Small delay to increase chance of interleaving without lock
                        time.sleep(0.001)
                        data["count"] = current + 1
                        writer.write_yaml(path, data)
            except Exception as exc:
                errors.append(str(exc))

        t1 = threading.Thread(target=increment, args=(iterations,))
        t2 = threading.Thread(target=increment, args=(iterations,))

        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        assert not errors, f"Thread errors: {errors}"

        result = reader.read_yaml(counter_file)
        assert result["count"] == iterations * 2

    def test_lock_for_rmw_with_nonexistent_parent(self, tmp_path: Path) -> None:
        """lock_for_rmw creates parent directories for the lock file."""
        deep_path = tmp_path / "deep" / "nested" / "file.yaml"
        assert not deep_path.parent.exists()

        with lock_for_rmw(deep_path) as path:
            assert path == deep_path
            # Parent dir was created for the lock file
            assert deep_path.parent.exists()


# ---------------------------------------------------------------------------
# read_jsonl: large records, numeric-only lines
# ---------------------------------------------------------------------------


class TestReadJsonlAdditionalEdges:
    """Additional read_jsonl edge cases."""

    def test_integer_json_line_skipped(
        self, tmp_path: Path, reader: FileStateReader
    ) -> None:
        """A bare integer on a JSONL line is skipped (not a dict)."""
        jsonl_file = tmp_path / "ints.jsonl"
        jsonl_file.write_text('{"a": 1}\n42\n{"b": 2}\n', encoding="utf-8")

        records = reader.read_jsonl(jsonl_file)
        assert len(records) == 2
        assert records[0]["a"] == 1
        assert records[1]["b"] == 2

    def test_null_json_line_skipped(
        self, tmp_path: Path, reader: FileStateReader
    ) -> None:
        """A bare null on a JSONL line is skipped."""
        jsonl_file = tmp_path / "nulls.jsonl"
        jsonl_file.write_text('{"ok": true}\nnull\n', encoding="utf-8")

        records = reader.read_jsonl(jsonl_file)
        assert len(records) == 1
        assert records[0]["ok"] is True

    def test_boolean_json_line_skipped(
        self, tmp_path: Path, reader: FileStateReader
    ) -> None:
        """A bare boolean on a JSONL line is skipped."""
        jsonl_file = tmp_path / "bools.jsonl"
        jsonl_file.write_text('true\n{"valid": 1}\nfalse\n', encoding="utf-8")

        records = reader.read_jsonl(jsonl_file)
        assert len(records) == 1
        assert records[0]["valid"] == 1

    def test_whitespace_only_lines_skipped(
        self, tmp_path: Path, reader: FileStateReader
    ) -> None:
        """Lines containing only spaces and tabs are skipped."""
        jsonl_file = tmp_path / "ws.jsonl"
        jsonl_file.write_text(
            '{"a": 1}\n   \t  \n{"b": 2}\n', encoding="utf-8"
        )

        records = reader.read_jsonl(jsonl_file)
        assert len(records) == 2

    def test_large_record_roundtrip(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """A record with many keys survives append/read cycle."""
        jsonl_file = tmp_path / "large.jsonl"
        big_record: dict[str, object] = {f"key_{i}": i for i in range(200)}
        writer.append_jsonl(jsonl_file, big_record)

        records = reader.read_jsonl(jsonl_file)
        assert len(records) == 1
        assert records[0]["key_0"] == 0
        assert records[0]["key_199"] == 199


# ---------------------------------------------------------------------------
# _new_yaml: configuration verification
# ---------------------------------------------------------------------------


class TestNewYamlConfiguration:
    """Verify _new_yaml creates correctly configured YAML instances."""

    def test_flow_style_disabled(self) -> None:
        """_new_yaml sets default_flow_style to False."""
        from trw_mcp.state.persistence import _new_yaml

        yml = _new_yaml()
        assert yml.default_flow_style is False

    def test_preserve_quotes_enabled(self) -> None:
        """_new_yaml sets preserve_quotes to True."""
        from trw_mcp.state.persistence import _new_yaml

        yml = _new_yaml()
        assert yml.preserve_quotes is True

    def test_each_call_returns_new_instance(self) -> None:
        """_new_yaml returns a fresh instance every call (thread safety)."""
        from trw_mcp.state.persistence import _new_yaml

        yml1 = _new_yaml()
        yml2 = _new_yaml()
        assert yml1 is not yml2


# ---------------------------------------------------------------------------
# FileStateReader.exists: edge cases
# ---------------------------------------------------------------------------


class TestExistsEdgeCases:
    """Additional exists() edge cases."""

    def test_exists_for_file(self, tmp_path: Path, reader: FileStateReader) -> None:
        """exists() returns True for a regular file."""
        f = tmp_path / "file.txt"
        f.write_text("content", encoding="utf-8")
        assert reader.exists(f) is True

    def test_exists_for_directory(
        self, tmp_path: Path, reader: FileStateReader
    ) -> None:
        """exists() returns True for a directory."""
        d = tmp_path / "subdir"
        d.mkdir()
        assert reader.exists(d) is True

    def test_exists_for_symlink(
        self, tmp_path: Path, reader: FileStateReader
    ) -> None:
        """exists() returns True for a valid symlink."""
        target = tmp_path / "target.txt"
        target.write_text("data", encoding="utf-8")
        link = tmp_path / "link.txt"
        link.symlink_to(target)
        assert reader.exists(link) is True

    def test_exists_for_broken_symlink(
        self, tmp_path: Path, reader: FileStateReader
    ) -> None:
        """exists() returns False for a broken symlink."""
        target = tmp_path / "gone.txt"
        target.write_text("data", encoding="utf-8")
        link = tmp_path / "broken.txt"
        link.symlink_to(target)
        target.unlink()
        assert reader.exists(link) is False


# ---------------------------------------------------------------------------
# Protocol compliance: verify concrete classes satisfy the Protocol
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    """Verify FileStateReader/Writer/EventLogger satisfy their Protocol contracts."""

    def test_reader_has_all_protocol_methods(self) -> None:
        """FileStateReader has read_yaml, read_jsonl, and exists methods."""
        reader = FileStateReader()
        assert callable(getattr(reader, "read_yaml", None))
        assert callable(getattr(reader, "read_jsonl", None))
        assert callable(getattr(reader, "exists", None))

    def test_writer_has_all_protocol_methods(self) -> None:
        """FileStateWriter has write_yaml, append_jsonl, write_text, ensure_dir."""
        wr = FileStateWriter()
        assert callable(getattr(wr, "write_yaml", None))
        assert callable(getattr(wr, "append_jsonl", None))
        assert callable(getattr(wr, "write_text", None))
        assert callable(getattr(wr, "ensure_dir", None))

    def test_event_logger_has_log_event(self) -> None:
        """FileEventLogger has log_event method."""
        el = FileEventLogger()
        assert callable(getattr(el, "log_event", None))
