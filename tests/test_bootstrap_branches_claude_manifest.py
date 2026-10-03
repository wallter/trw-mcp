"""Split bootstrap branch coverage for manifest helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.bootstrap import (
    _read_manifest,
    _write_manifest,
)


@pytest.mark.unit
class TestReadManifest:
    """Cover _read_manifest edge cases."""

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        """Returns None when manifest file doesn't exist."""
        result = _read_manifest(tmp_path)
        assert result is None

    def test_returns_none_when_not_dict(self, tmp_path: Path) -> None:
        """Returns None when read_yaml returns a non-dict (e.g. a list)."""
        manifest_path = tmp_path / ".trw" / "managed-artifacts.yaml"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text("- not\n- a\n- dict\n", encoding="utf-8")

        mock_reader = MagicMock()
        mock_reader.read_yaml.return_value = ["not", "a", "dict"]
        with patch("trw_mcp.state.persistence.FileStateReader", return_value=mock_reader):
            result = _read_manifest(tmp_path)
        assert result is None
        assert result != {"skills": [], "agents": [], "hooks": []}
        assert mock_reader.read_yaml.call_count == 1

        # Contrast: the same file with a real mapping is accepted.
        manifest_path.write_text("version: 1\nskills: [deliver]\n", encoding="utf-8")
        parsed = _read_manifest(tmp_path)
        assert parsed is not None
        assert parsed["skills"] == ["deliver"]

    def test_returns_none_on_oserror(self, tmp_path: Path) -> None:
        """Returns None when OSError reading manifest."""
        manifest_path = tmp_path / ".trw" / "managed-artifacts.yaml"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text("version: 1\nskills: []\n", encoding="utf-8")

        with patch("trw_mcp.state.persistence.FileStateReader.read_yaml", side_effect=OSError("io error")):
            result = _read_manifest(tmp_path)
        assert result is None
        assert result != {"skills": [], "agents": [], "hooks": []}

        # Contrast: without the injected I/O error the same file reads back.
        parsed = _read_manifest(tmp_path)
        assert parsed is not None
        assert parsed["version"] == 1

    def test_returns_dict_with_lists(self, tmp_path: Path) -> None:
        """Returns dict with skills/agents/hooks lists."""
        manifest_path = tmp_path / ".trw" / "managed-artifacts.yaml"
        manifest_path.parent.mkdir(parents=True)
        from trw_mcp.state.persistence import FileStateWriter

        FileStateWriter().write_yaml(
            manifest_path,
            {
                "version": 1,
                "skills": ["deliver", "learn"],
                "agents": ["trw-tester.md"],
                "hooks": ["session-start.sh"],
            },
        )

        result = _read_manifest(tmp_path)
        assert result is not None
        assert "deliver" in result["skills"]
        assert "trw-tester.md" in result["agents"]


@pytest.mark.unit
class TestWriteManifest:
    """Cover _write_manifest error path."""

    def test_write_manifest_oserror(self, tmp_path: Path) -> None:
        """OSError writing manifest adds to errors."""
        result: dict[str, list[str]] = {"created": [], "errors": []}

        with patch("trw_mcp.state.persistence.FileStateWriter.write_yaml", side_effect=OSError("disk full")):
            _write_manifest(tmp_path, result)

        assert any("Failed to write manifest" in e for e in result["errors"])

    def test_write_manifest_uses_updated_key_when_present(self, tmp_path: Path) -> None:
        """When 'updated' key exists in result, manifest is appended there."""
        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        (tmp_path / ".trw").mkdir(parents=True)

        _write_manifest(tmp_path, result)

        assert any("managed-artifacts" in u for u in result["updated"])
        assert not result["errors"]
