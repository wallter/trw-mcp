"""Split bootstrap version/default-config helper tests."""

from __future__ import annotations

import importlib.metadata
from datetime import datetime
from pathlib import Path

import pytest

from trw_mcp.bootstrap._utils import _result_action_key, _write_version_yaml
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader

from ._bootstrap_test_support import fake_git_repo  # noqa: F401


class TestDefaultConfig:
    """Test _default_config() matches TRWConfig defaults."""

    def test_default_config_matches_trwconfig(self) -> None:
        """_default_config() claude_md_max_lines matches TRWConfig default."""
        from trw_mcp.bootstrap import _default_config
        from trw_mcp.models.config import TRWConfig

        config_text = _default_config()
        default_model = TRWConfig()
        assert f"claude_md_max_lines: {default_model.claude_md_max_lines}" in config_text

    def test_default_config_includes_runs_root(self) -> None:
        """_default_config() includes runs_root with the default value."""
        from trw_mcp.bootstrap import _default_config

        config_text = _default_config()
        assert "runs_root: .trw/runs" in config_text

    def test_default_config_custom_runs_root(self) -> None:
        """_default_config(runs_root=...) emits the custom value."""
        from trw_mcp.bootstrap import _default_config

        config_text = _default_config(runs_root="docs/runs")
        assert "runs_root: docs/runs" in config_text
        assert ".trw/runs" not in config_text


class TestWriteVersionYaml:
    """Unit tests for _write_version_yaml — VERSION.yaml generation from metadata."""

    def _make_init_result(self) -> dict[str, list[str]]:
        """Return a result dict matching init_project's shape (no 'updated' key)."""
        return {"created": [], "skipped": [], "errors": []}

    def _make_update_result(self) -> dict[str, list[str]]:
        """Return a result dict matching update_project's shape (has 'updated' key)."""
        return {"created": [], "updated": [], "skipped": [], "errors": [], "preserved": []}

    def test_writes_all_expected_keys(self, fake_git_repo: Path) -> None:
        """Generated VERSION.yaml contains all four expected metadata keys."""
        (fake_git_repo / ".trw" / "frameworks").mkdir(parents=True)
        result = self._make_init_result()
        _write_version_yaml(fake_git_repo, result)

        version_path = fake_git_repo / ".trw" / "frameworks" / "VERSION.yaml"
        assert version_path.is_file()

        data = FileStateReader().read_yaml(version_path)
        assert isinstance(data, dict)
        assert "framework_version" in data
        assert "aaref_version" in data
        assert "trw_mcp_version" in data
        assert "deployed_at" in data

    def test_framework_version_matches_config(self, fake_git_repo: Path) -> None:
        """framework_version in VERSION.yaml matches TRWConfig default."""
        (fake_git_repo / ".trw" / "frameworks").mkdir(parents=True)
        result = self._make_init_result()
        _write_version_yaml(fake_git_repo, result)

        version_path = fake_git_repo / ".trw" / "frameworks" / "VERSION.yaml"
        data = FileStateReader().read_yaml(version_path)
        assert isinstance(data, dict)
        assert data["framework_version"] == TRWConfig().framework_version

    def test_trw_mcp_version_matches_metadata(self, fake_git_repo: Path) -> None:
        """trw_mcp_version in VERSION.yaml matches installed package metadata."""
        (fake_git_repo / ".trw" / "frameworks").mkdir(parents=True)
        result = self._make_init_result()
        _write_version_yaml(fake_git_repo, result)

        version_path = fake_git_repo / ".trw" / "frameworks" / "VERSION.yaml"
        data = FileStateReader().read_yaml(version_path)
        assert isinstance(data, dict)
        assert data["trw_mcp_version"] == importlib.metadata.version("trw-mcp")

    def test_deployed_at_is_valid_iso(self, fake_git_repo: Path) -> None:
        """deployed_at field parses as a valid ISO-8601 datetime without error."""
        (fake_git_repo / ".trw" / "frameworks").mkdir(parents=True)
        result = self._make_init_result()
        _write_version_yaml(fake_git_repo, result)

        version_path = fake_git_repo / ".trw" / "frameworks" / "VERSION.yaml"
        data = FileStateReader().read_yaml(version_path)
        assert isinstance(data, dict)
        deployed_at = data["deployed_at"]
        # fromisoformat raises ValueError on invalid input — that's the assertion
        parsed = datetime.fromisoformat(str(deployed_at))
        assert parsed is not None

    def test_appends_to_created_for_init_result(self, fake_git_repo: Path) -> None:
        """On an init-style result (no 'updated' key), path is appended to result['created']."""
        (fake_git_repo / ".trw" / "frameworks").mkdir(parents=True)
        result = self._make_init_result()
        _write_version_yaml(fake_git_repo, result)

        version_path = str(fake_git_repo / ".trw" / "frameworks" / "VERSION.yaml")
        assert version_path in result["created"]
        assert result["errors"] == []

    def test_appends_to_updated_for_update_result(self, fake_git_repo: Path) -> None:
        """On an update-style result (has 'updated' key), path is appended to result['updated']."""
        (fake_git_repo / ".trw" / "frameworks").mkdir(parents=True)
        result = self._make_update_result()
        _write_version_yaml(fake_git_repo, result)

        version_path = str(fake_git_repo / ".trw" / "frameworks" / "VERSION.yaml")
        assert version_path in result["updated"]
        # Should NOT also appear in created
        assert version_path not in result["created"]
        assert result["errors"] == []

    def test_oserror_captured_in_errors(self, fake_git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """OSError from FileStateWriter.write_yaml is captured in result['errors']."""
        (fake_git_repo / ".trw" / "frameworks").mkdir(parents=True)
        result = self._make_init_result()

        from trw_mcp.state import persistence as persistence_mod

        def _raise_os_error(self: object, path: Path, data: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(persistence_mod.FileStateWriter, "write_yaml", _raise_os_error)
        _write_version_yaml(fake_git_repo, result)

        assert len(result["errors"]) == 1
        assert "disk full" in result["errors"][0]
        assert result["created"] == []


# ── _result_action_key Tests ─────────────────────────────────────────────


@pytest.mark.unit
class TestResultActionKey:
    """Unit tests for _result_action_key — action-key selection helper."""

    def test_returns_created_when_no_updated_key(self) -> None:
        """Returns 'created' when result dict has no 'updated' key (init flow)."""
        result: dict[str, list[str]] = {"created": [], "errors": []}
        assert _result_action_key(result) == "created"

    def test_returns_updated_when_updated_key_exists(self) -> None:
        """Returns 'updated' when result dict contains an 'updated' key (update flow)."""
        result: dict[str, list[str]] = {"created": [], "updated": [], "errors": []}
        assert _result_action_key(result) == "updated"
