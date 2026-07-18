"""Focused reader and protocol edge-case tests for state/persistence.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.exceptions import StateError
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter


class TestReadJsonlAdditionalEdges:
    """Additional read_jsonl edge cases."""

    def test_integer_json_line_skipped(self, tmp_path: Path, reader: FileStateReader) -> None:
        """A bare integer on a JSONL line is skipped (not a dict)."""
        jsonl_file = tmp_path / "ints.jsonl"
        jsonl_file.write_text('{"a": 1}\n42\n{"b": 2}\n', encoding="utf-8")

        records = reader.read_jsonl(jsonl_file)
        assert len(records) == 2
        assert records[0]["a"] == 1
        assert records[1]["b"] == 2

    def test_null_json_line_skipped(self, tmp_path: Path, reader: FileStateReader) -> None:
        """A bare null on a JSONL line is skipped."""
        jsonl_file = tmp_path / "nulls.jsonl"
        jsonl_file.write_text('{"ok": true}\nnull\n', encoding="utf-8")

        records = reader.read_jsonl(jsonl_file)
        assert len(records) == 1
        assert records[0]["ok"] is True

    def test_boolean_json_line_skipped(self, tmp_path: Path, reader: FileStateReader) -> None:
        """A bare boolean on a JSONL line is skipped."""
        jsonl_file = tmp_path / "bools.jsonl"
        jsonl_file.write_text('true\n{"valid": 1}\nfalse\n', encoding="utf-8")

        records = reader.read_jsonl(jsonl_file)
        assert len(records) == 1
        assert records[0]["valid"] == 1

    def test_whitespace_only_lines_skipped(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Lines containing only spaces and tabs are skipped."""
        jsonl_file = tmp_path / "ws.jsonl"
        jsonl_file.write_text('{"a": 1}\n   \t  \n{"b": 2}\n', encoding="utf-8")

        records = reader.read_jsonl(jsonl_file)
        assert len(records) == 2

    def test_large_record_roundtrip(self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """A record with many keys survives append/read cycle."""
        jsonl_file = tmp_path / "large.jsonl"
        big_record: dict[str, object] = {f"key_{i}": i for i in range(200)}
        writer.append_jsonl(jsonl_file, big_record)

        records = reader.read_jsonl(jsonl_file)
        assert len(records) == 1
        assert records[0]["key_0"] == 0
        assert records[0]["key_199"] == 199


class TestExistsEdgeCases:
    """Additional exists() edge cases."""

    def test_exists_for_file(self, tmp_path: Path, reader: FileStateReader) -> None:
        """exists() returns True for a regular file."""
        f = tmp_path / "file.txt"
        f.write_text("content", encoding="utf-8")
        assert reader.exists(f) is True

    def test_exists_for_directory(self, tmp_path: Path, reader: FileStateReader) -> None:
        """exists() returns True for a directory."""
        d = tmp_path / "subdir"
        d.mkdir()
        assert reader.exists(d) is True

    def test_exists_for_symlink(self, tmp_path: Path, reader: FileStateReader) -> None:
        """exists() returns True for a valid symlink."""
        target = tmp_path / "target.txt"
        target.write_text("data", encoding="utf-8")
        link = tmp_path / "link.txt"
        link.symlink_to(target)
        assert reader.exists(link) is True

    def test_exists_for_broken_symlink(self, tmp_path: Path, reader: FileStateReader) -> None:
        """exists() returns False for a broken symlink."""
        target = tmp_path / "gone.txt"
        target.write_text("data", encoding="utf-8")
        link = tmp_path / "broken.txt"
        link.symlink_to(target)
        target.unlink()
        assert reader.exists(link) is False

    def test_symlink_loop_is_typed_content_free_state_error(self, tmp_path: Path) -> None:
        loop = tmp_path / "loop.yaml"
        loop.symlink_to(loop)
        reader = FileStateReader(base_dir=tmp_path)

        with pytest.raises(StateError, match="state read path resolution failed: RuntimeError") as exc_info:
            reader.exists(loop)

        assert exc_info.value.context["path"] == str(loop)
        assert "Symlink loop" not in str(exc_info.value)


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
