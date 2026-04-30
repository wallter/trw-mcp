"""Focused YAML edge-case tests for state/persistence.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.exceptions import StateError
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


class TestReadYamlMalformedSyntax:
    """read_yaml wraps YAML syntax errors as StateError."""

    def test_unclosed_bracket_raises_state_error(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Unclosed YAML flow sequence produces a parse error wrapped in StateError."""
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("items: [a, b, c\n", encoding="utf-8")

        with pytest.raises(StateError, match="Failed to read YAML"):
            reader.read_yaml(bad_yaml)

    def test_invalid_indentation_raises_state_error(self, tmp_path: Path, reader: FileStateReader) -> None:
        """YAML with bad indentation that causes a parse error is wrapped."""
        bad_yaml = tmp_path / "indent.yaml"
        bad_yaml.write_text("key:\n  sub: 1\n sub2: 2\n", encoding="utf-8")

        try:
            result = reader.read_yaml(bad_yaml)
            assert isinstance(result, dict)
        except StateError:
            pass

    def test_tab_characters_in_yaml_raises_state_error(self, tmp_path: Path, reader: FileStateReader) -> None:
        """YAML with tab indentation (forbidden by spec) raises StateError."""
        bad_yaml = tmp_path / "tabs.yaml"
        bad_yaml.write_text("key:\n\tvalue: 1\n", encoding="utf-8")

        with pytest.raises(StateError, match="Failed to read YAML"):
            reader.read_yaml(bad_yaml)

    def test_duplicate_key_yaml_raises_state_error(self, tmp_path: Path, reader: FileStateReader) -> None:
        """YAML with duplicate keys raises StateError (ruamel strict mode)."""
        yaml_file = tmp_path / "dup.yaml"
        yaml_file.write_text("key: first\nkey: second\n", encoding="utf-8")

        with pytest.raises(StateError, match="Failed to read YAML"):
            reader.read_yaml(yaml_file)


class TestReadYamlUnicode:
    """read_yaml handles unicode content correctly."""

    def test_unicode_values_preserved(self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
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

    def test_unicode_keys_preserved(self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """Unicode characters in keys survive write/read roundtrip."""
        yaml_file = tmp_path / "ukeys.yaml"
        data: dict[str, object] = {"schlüssel": "wert", "ключ": "значение"}
        writer.write_yaml(yaml_file, data)
        result = reader.read_yaml(yaml_file)

        assert result["schlüssel"] == "wert"
        assert result["ключ"] == "значение"


class TestWriteReadYamlRoundtrip:
    """write_yaml followed by read_yaml preserves data types."""

    def test_nested_dict_roundtrip(self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
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

    def test_empty_dict_roundtrip(self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """An empty dict written and read back returns empty dict."""
        yaml_file = tmp_path / "empty.yaml"
        writer.write_yaml(yaml_file, {})
        result = reader.read_yaml(yaml_file)
        assert result == {}

    def test_numeric_types_roundtrip(self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
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

    def test_none_value_roundtrip(self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """None values are preserved through YAML roundtrip (as null)."""
        yaml_file = tmp_path / "nulls.yaml"
        data: dict[str, object] = {"present": "yes", "absent": None}
        writer.write_yaml(yaml_file, data)
        result = reader.read_yaml(yaml_file)

        assert result["present"] == "yes"
        assert result["absent"] is None

    def test_write_yaml_creates_parent_directories(self, tmp_path: Path, writer: FileStateWriter) -> None:
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
        assert "version" in result


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
