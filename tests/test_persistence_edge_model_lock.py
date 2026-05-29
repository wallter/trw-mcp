"""Focused model and locking edge-case tests for state/persistence.py."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from trw_mcp.state.persistence import FileStateReader, FileStateWriter, lock_for_rmw, model_to_dict


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
            assert deep_path.parent.exists()
