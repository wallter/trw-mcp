"""Extra coverage tests for state/persistence.py — targeting uncovered branches.

Covers:
- Lines 84-85: json_serializer TypeError for unsupported types
- Line 115: read_yaml non-dict YAML root raises StateError
- Line 122: read_yaml except StateError: raise re-raise path
- Line 156: read_jsonl non-dict line triggers logger.warning
- Lines 169-172: read_jsonl JSONDecodeError and generic Exception paths
- Lines 221-223: write_yaml temp file cleanup on exception
- Lines 227-228: write_yaml os.close(fd) finally block
- Lines 230-233: write_yaml StateError re-raise and outer exception
- Lines 259-260: append_jsonl StateError on exception
- Lines 290-292: write_text temp file cleanup on BaseException
- Lines 296-301: write_text os.close finally and StateError re-raise
- Lines 317-318: ensure_dir StateError on mkdir failure
- Lines 338-346: lock_for_rmw context manager
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.exceptions import StateError
from trw_mcp.state.persistence import (
    FileStateReader,
    FileStateWriter,
    json_serializer,
    lock_for_rmw,
)

# ---------------------------------------------------------------------------
# Lines 84-85: json_serializer TypeError
# ---------------------------------------------------------------------------


class TestJsonSerializer:
    """Tests for json_serializer custom JSON encoder."""

    def test_datetime_serialized(self) -> None:
        """datetime objects are serialized to ISO format."""
        dt = datetime(2026, 2, 22, 12, 0, 0, tzinfo=timezone.utc)
        result = json_serializer(dt)
        assert result == "2026-02-22T12:00:00+00:00"

    def test_date_serialized(self) -> None:
        """date objects are serialized to ISO format."""
        d = date(2026, 2, 22)
        result = json_serializer(d)
        assert result == "2026-02-22"

    def test_unsupported_type_raises_typeerror(self) -> None:
        """Lines 84-85: unsupported type raises TypeError with type name."""
        with pytest.raises(TypeError, match="set"):
            json_serializer({1, 2, 3})

    def test_object_raises_typeerror(self) -> None:
        """Custom objects raise TypeError."""

        class MyObj:
            pass

        with pytest.raises(TypeError, match="MyObj"):
            json_serializer(MyObj())


# ---------------------------------------------------------------------------
# Line 115: read_yaml non-dict YAML root
# ---------------------------------------------------------------------------


class TestReadYamlNonDict:
    """Tests for FileStateReader.read_yaml with non-dict content."""

    def test_yaml_list_root_raises_state_error(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Line 115: YAML file with list root raises StateError."""
        yaml_file = tmp_path / "list.yaml"
        yaml_file.write_text("- item1\n- item2\n", encoding="utf-8")

        with pytest.raises(StateError, match="mapping"):
            reader.read_yaml(yaml_file)

    def test_yaml_scalar_root_raises_state_error(self, tmp_path: Path, reader: FileStateReader) -> None:
        """YAML file with scalar root raises StateError."""
        yaml_file = tmp_path / "scalar.yaml"
        yaml_file.write_text("just a string\n", encoding="utf-8")

        with pytest.raises(StateError, match="mapping"):
            reader.read_yaml(yaml_file)

    def test_yaml_none_returns_empty_dict(self, tmp_path: Path, reader: FileStateReader) -> None:
        """YAML file with null/empty content returns empty dict."""
        yaml_file = tmp_path / "empty.yaml"
        yaml_file.write_text("", encoding="utf-8")

        result = reader.read_yaml(yaml_file)
        assert result == {}

    def test_yaml_not_found_raises_state_error(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Missing YAML file raises StateError."""
        with pytest.raises(StateError, match="not found"):
            reader.read_yaml(tmp_path / "nonexistent.yaml")


# ---------------------------------------------------------------------------
# Line 122: read_yaml except StateError: raise re-raise path
# ---------------------------------------------------------------------------


class TestReadYamlStateErrorReRaise:
    """Lines 122-127: StateError re-raise and generic Exception wrapping."""

    def test_state_error_propagates_from_yaml_load(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Line 122: StateError from _new_yaml().load() propagates without wrapping."""
        yaml_file = tmp_path / "file.yaml"
        yaml_file.write_text("key: value\n", encoding="utf-8")

        original_error = StateError("inner error", path=str(yaml_file))

        mock_yaml = MagicMock()
        mock_yaml.load.side_effect = original_error

        with patch("trw_mcp.state.persistence._safe_yaml", return_value=mock_yaml):
            with pytest.raises(StateError, match="inner error"):
                reader.read_yaml(yaml_file)

    def test_generic_exception_wraps_to_state_error(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Lines 123-127: Generic Exception from load() wraps to StateError."""
        yaml_file = tmp_path / "file.yaml"
        yaml_file.write_text("key: value\n", encoding="utf-8")

        mock_yaml = MagicMock()
        mock_yaml.load.side_effect = RuntimeError("encoding error")

        with patch("trw_mcp.state.persistence._safe_yaml", return_value=mock_yaml):
            with pytest.raises(StateError, match="Failed to read YAML"):
                reader.read_yaml(yaml_file)


class TestFileStateReaderExists:
    """Line 186: FileStateReader.exists() coverage."""

    def test_exists_returns_true_for_existing_path(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Line 186: exists() returns True when path exists."""
        assert reader.exists(tmp_path) is True

    def test_exists_returns_false_for_missing_path(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Line 186: exists() returns False when path does not exist."""
        assert reader.exists(tmp_path / "no_such_file.txt") is False


# ---------------------------------------------------------------------------
# Line 156: read_jsonl non-dict line warning
# ---------------------------------------------------------------------------


class TestReadJsonlNonDictLine:
    """Line 156: non-dict JSON lines in JSONL trigger logger.warning."""

    def test_non_dict_line_skipped_with_warning(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Non-dict JSON lines are skipped and trigger a warning log."""
        jsonl_file = tmp_path / "data.jsonl"
        # Mix: valid dict, list (non-dict), another valid dict
        jsonl_file.write_text(
            '{"key": "value1"}\n[1, 2, 3]\n{"key": "value2"}\n',
            encoding="utf-8",
        )

        with patch("trw_mcp.state.persistence.logger") as mock_logger:
            records = reader.read_jsonl(jsonl_file)

        # Only dict records returned
        assert len(records) == 2
        assert records[0] == {"key": "value1"}
        assert records[1] == {"key": "value2"}
        # Warning was logged for the list line
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert "jsonl_non_dict_line" in call_args[0]

    def test_scalar_jsonl_line_skipped(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Scalar JSON values in JSONL are also skipped."""
        jsonl_file = tmp_path / "data.jsonl"
        jsonl_file.write_text(
            '{"a": 1}\n"just a string"\n',
            encoding="utf-8",
        )

        records = reader.read_jsonl(jsonl_file)
        assert len(records) == 1
        assert records[0] == {"a": 1}


# ---------------------------------------------------------------------------
# Lines 169-172: read_jsonl JSONDecodeError and generic Exception
# ---------------------------------------------------------------------------


class TestReadJsonlErrorPaths:
    """Tests for read_jsonl error handling branches."""

    def test_json_decode_error_raises_state_error(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Lines 164-168: JSONDecodeError wraps to StateError."""
        jsonl_file = tmp_path / "corrupt.jsonl"
        jsonl_file.write_text("not valid json {\n", encoding="utf-8")

        with pytest.raises(StateError, match="JSONL"):
            reader.read_jsonl(jsonl_file)

    def test_generic_exception_raises_state_error(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Lines 170-172: Generic exception wraps to StateError."""
        jsonl_file = tmp_path / "data.jsonl"
        jsonl_file.write_text('{"key": "val"}\n', encoding="utf-8")

        with patch(
            "trw_mcp.state.persistence.json.loads",
            side_effect=RuntimeError("disk error"),
        ):
            with pytest.raises(StateError, match="JSONL"):
                reader.read_jsonl(jsonl_file)

    def test_blank_lines_in_jsonl_skipped(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Line 151: blank lines inside JSONL file are skipped (continue)."""
        jsonl_file = tmp_path / "data.jsonl"
        jsonl_file.write_text(
            '{"a": 1}\n\n   \n{"b": 2}\n',
            encoding="utf-8",
        )

        records = reader.read_jsonl(jsonl_file)
        assert len(records) == 2
        assert records[0] == {"a": 1}
        assert records[1] == {"b": 2}

    def test_state_error_from_jsonl_propagates(self, tmp_path: Path, reader: FileStateReader) -> None:
        """StateError raised inside read_jsonl propagates unchanged."""
        jsonl_file = tmp_path / "data.jsonl"
        jsonl_file.write_text('{"key": "val"}\n', encoding="utf-8")

        original_err = StateError("jsonl state error", path=str(jsonl_file))
        with patch(
            "trw_mcp.state.persistence.json.loads",
            side_effect=original_err,
        ):
            with pytest.raises(StateError, match="jsonl state error"):
                reader.read_jsonl(jsonl_file)

    def test_empty_file_returns_empty_list(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Empty JSONL file returns empty list."""
        jsonl_file = tmp_path / "empty.jsonl"
        jsonl_file.write_text("", encoding="utf-8")

        result = reader.read_jsonl(jsonl_file)
        assert result == []

    def test_nonexistent_jsonl_returns_empty_list(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Missing JSONL file returns empty list."""
        result = reader.read_jsonl(tmp_path / "missing.jsonl")
        assert result == []


# ---------------------------------------------------------------------------
# Lines 221-223 + 227-228: write_yaml temp file cleanup on exception
# ---------------------------------------------------------------------------


class TestWriteYamlCleanup:
    """Tests for write_yaml atomic write failure and cleanup paths."""

    def test_write_yaml_cleans_up_tmp_on_error(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Lines 221-223: if write raises, tmp file is removed."""
        yaml_file = tmp_path / "output.yaml"

        # Patch _new_yaml().dump to raise, simulating write failure
        mock_yaml = MagicMock()
        mock_yaml.dump.side_effect = OSError("disk full")

        with patch("trw_mcp.state.persistence._roundtrip_yaml", return_value=mock_yaml):
            with pytest.raises(StateError, match="Failed to write YAML"):
                writer.write_yaml(yaml_file, {"key": "value"})

        # The yaml file should NOT exist (either cleaned up or never renamed)
        assert not yaml_file.exists()
        # No stray .tmp files
        tmp_files = list(tmp_path.glob("*.yaml.tmp"))
        assert len(tmp_files) == 0

    def test_write_yaml_oserror_on_close_fd(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Lines 227-228: os.close() raising OSError is silenced."""
        yaml_file = tmp_path / "output.yaml"

        with patch("trw_mcp.state.persistence.os.close", side_effect=OSError("bad fd")):
            # Should NOT raise — OSError from os.close is caught and ignored
            writer.write_yaml(yaml_file, {"key": "value"})

        assert yaml_file.exists()

    def test_write_yaml_state_error_reraise(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Lines 230-231: StateError from inner block propagates unchanged."""
        yaml_file = tmp_path / "output.yaml"

        original_err = StateError("inner yaml error", path=str(yaml_file))
        mock_yaml = MagicMock()
        mock_yaml.dump.side_effect = original_err

        with patch("trw_mcp.state.persistence._roundtrip_yaml", return_value=mock_yaml):
            with pytest.raises(StateError, match="inner yaml error"):
                writer.write_yaml(yaml_file, {"key": "value"})

    def test_write_yaml_wraps_generic_exception(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Lines 232-236: Generic exception wraps to StateError."""
        yaml_file = tmp_path / "output.yaml"

        mock_yaml = MagicMock()
        mock_yaml.dump.side_effect = ValueError("bad data")

        with patch("trw_mcp.state.persistence._roundtrip_yaml", return_value=mock_yaml):
            with pytest.raises(StateError, match="Failed to write YAML"):
                writer.write_yaml(yaml_file, {"key": "value"})


# ---------------------------------------------------------------------------
# Lines 259-260: append_jsonl StateError
# ---------------------------------------------------------------------------


class TestAppendJsonlErrorPaths:
    """Tests for append_jsonl error handling."""

    def test_append_jsonl_raises_state_error_on_failure(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Lines 259-260: exception in append_jsonl wraps to StateError."""
        jsonl_file = tmp_path / "events.jsonl"
        jsonl_file.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(Path, "open", side_effect=OSError("permission denied")):
            with pytest.raises(StateError, match="Failed to append JSONL"):
                writer.append_jsonl(jsonl_file, {"key": "value"})

    def test_append_jsonl_datetime_serialization(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """append_jsonl correctly serializes datetime values via json_serializer."""
        jsonl_file = tmp_path / "events.jsonl"

        dt = datetime(2026, 2, 22, 0, 0, 0, tzinfo=timezone.utc)
        writer.append_jsonl(jsonl_file, {"ts": dt, "event": "test_event"})

        records = json.loads(jsonl_file.read_text())
        assert records["ts"] == "2026-02-22T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Lines 290-301: write_text cleanup + finally block
# ---------------------------------------------------------------------------


class TestWriteTextCleanup:
    """Tests for write_text atomic write failure and cleanup paths."""

    def test_write_text_cleans_up_tmp_on_base_exception(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Lines 290-292: BaseException during write cleans up tmp file."""
        text_file = tmp_path / "output.md"

        original_open = Path.open

        def patched_open(self: Path, mode: str = "r", **kwargs: object) -> object:
            if "w" in mode and str(self).endswith(".tmp"):
                raise RuntimeError("write failed")
            return original_open(self, mode, **kwargs)

        with patch.object(Path, "open", patched_open):
            with pytest.raises(StateError, match="Failed to write text"):
                writer.write_text(text_file, "hello world")

        # Target file should not exist
        assert not text_file.exists()
        # No stray .tmp files
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_write_text_oserror_on_close_fd_silenced(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Lines 296-298: os.close() raising OSError is silenced in write_text."""
        text_file = tmp_path / "output.md"

        with patch("trw_mcp.state.persistence.os.close", side_effect=OSError("bad fd")):
            writer.write_text(text_file, "some content")

        assert text_file.read_text() == "some content"

    def test_write_text_state_error_reraise(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Lines 298-299: StateError propagates unchanged from write_text."""
        text_file = tmp_path / "output.md"

        original_err = StateError("inner text error", path=str(text_file))
        original_open = Path.open

        def patched_open(self: Path, mode: str = "r", **kwargs: object) -> object:
            if "w" in mode and str(self).endswith(".tmp"):
                raise original_err
            return original_open(self, mode, **kwargs)

        with patch.object(Path, "open", patched_open):
            with pytest.raises(StateError, match="inner text error"):
                writer.write_text(text_file, "hello")

    def test_write_text_wraps_generic_exception(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Lines 300-304: Generic exception wraps to StateError in write_text."""
        text_file = tmp_path / "output.md"

        original_open = Path.open

        def patched_open(self: Path, mode: str = "r", **kwargs: object) -> object:
            if "w" in mode and str(self).endswith(".tmp"):
                raise ValueError("encoding error")
            return original_open(self, mode, **kwargs)

        with patch.object(Path, "open", patched_open):
            with pytest.raises(StateError, match="Failed to write text"):
                writer.write_text(text_file, "hello")


# ---------------------------------------------------------------------------
# Lines 317-318: ensure_dir StateError
# ---------------------------------------------------------------------------


class TestEnsureDirErrorPath:
    """Tests for ensure_dir error handling."""

    def test_ensure_dir_raises_state_error_on_failure(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Lines 317-318: mkdir failure wraps to StateError."""
        with patch.object(Path, "mkdir", side_effect=PermissionError("access denied")):
            with pytest.raises(StateError, match="Failed to create directory"):
                writer.ensure_dir(tmp_path / "restricted" / "subdir")

    def test_ensure_dir_success(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """Happy path: directory is created."""
        target = tmp_path / "new" / "nested" / "dir"
        writer.ensure_dir(target)
        assert target.is_dir()

    def test_ensure_dir_idempotent(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """ensure_dir does not raise when directory already exists."""
        target = tmp_path / "existing"
        target.mkdir()
        writer.ensure_dir(target)  # Should not raise
        assert target.is_dir()


# ---------------------------------------------------------------------------
# Lines 338-346: lock_for_rmw context manager
# ---------------------------------------------------------------------------


class TestLockForRmw:
    """Tests for the lock_for_rmw advisory locking context manager."""

    def test_yields_original_path(self, tmp_path: Path) -> None:
        """Lines 343: lock_for_rmw yields the original path."""
        target = tmp_path / "data.yaml"
        with lock_for_rmw(target) as path:
            assert path == target

    def test_lock_file_created(self, tmp_path: Path) -> None:
        """Lines 338-340: .lock sibling file is created."""
        target = tmp_path / "data.yaml"
        with lock_for_rmw(target):
            lock_path = tmp_path / "data.yaml.lock"
            assert lock_path.exists()

    def test_lock_released_after_context(self, tmp_path: Path) -> None:
        """Lines 344-346: lock is released after context exits normally."""
        target = tmp_path / "data.yaml"
        with lock_for_rmw(target):
            pass
        # Can acquire again immediately (lock was released)
        with lock_for_rmw(target):
            pass

    def test_lock_released_on_exception(self, tmp_path: Path) -> None:
        """Finally block releases lock even when exception is raised."""
        target = tmp_path / "data.yaml"
        with pytest.raises(ValueError, match="test error"):
            with lock_for_rmw(target):
                raise ValueError("test error")

        # Lock should be released — can acquire again
        with lock_for_rmw(target) as path:
            assert path == target

    def test_nested_dirs_created(self, tmp_path: Path) -> None:
        """Lines 339: parent directories created if needed."""
        target = tmp_path / "sub" / "dir" / "data.yaml"
        with lock_for_rmw(target) as path:
            assert path == target
        lock_path = tmp_path / "sub" / "dir" / "data.yaml.lock"
        assert lock_path.exists()
