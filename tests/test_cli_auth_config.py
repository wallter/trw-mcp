"""Tests for CLI auth config persistence behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.cli.auth import (
    _save_api_key,
    _save_config_field,
    device_auth_logout,
    device_auth_status,
    run_auth_login,
)


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """Return a temporary config file with a synthetic API key fixture."""
    cfg = tmp_path / ".trw" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        'installation_id: "test"\nplatform_api_key: "trw_dk_existing123"\n',
        encoding="utf-8",
    )
    return cfg


class TestDeviceAuthLogout:
    def test_removes_key(self, config_file: Path) -> None:
        result = device_auth_logout(config_file)
        assert result is True
        content = config_file.read_text(encoding="utf-8")
        assert 'platform_api_key: ""' in content

    def test_no_key_present(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".trw" / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            'installation_id: "test"\nplatform_telemetry_enabled: true\n',
            encoding="utf-8",
        )
        result = device_auth_logout(cfg)
        assert result is False

    def test_no_config_file(self, tmp_path: Path) -> None:
        result = device_auth_logout(tmp_path / "nonexistent" / "config.yaml")
        assert result is False

    def test_empty_key_not_removed(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".trw" / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            'installation_id: "test"\nplatform_api_key: ""\n',
            encoding="utf-8",
        )
        result = device_auth_logout(cfg)
        assert result is False


class TestDeviceAuthStatus:
    def test_authenticated(self, config_file: Path) -> None:
        status = device_auth_status(config_file, "https://api.example.com")
        assert status["authenticated"] is True
        assert "key_prefix" in status
        assert status["key_prefix"].startswith("trw_dk_exi")

    def test_not_authenticated(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".trw" / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('installation_id: "test"\n', encoding="utf-8")
        status = device_auth_status(cfg, "https://api.example.com")
        assert status["authenticated"] is False

    def test_missing_file(self, tmp_path: Path) -> None:
        status = device_auth_status(
            tmp_path / "nonexistent" / "config.yaml",
            "https://api.example.com",
        )
        assert status["authenticated"] is False

    def test_empty_key(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".trw" / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('platform_api_key: ""\n', encoding="utf-8")
        status = device_auth_status(cfg, "https://api.example.com")
        assert status["authenticated"] is False


class TestSaveApiKey:
    def test_creates_new_file(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".trw" / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        _save_api_key(cfg, "trw_dk_newkey")
        content = cfg.read_text(encoding="utf-8")
        assert 'platform_api_key: "trw_dk_newkey"' in content

    def test_updates_existing_key(self, config_file: Path) -> None:
        _save_api_key(config_file, "trw_dk_updated")
        content = config_file.read_text(encoding="utf-8")
        assert 'platform_api_key: "trw_dk_updated"' in content
        assert "trw_dk_existing123" not in content

    def test_appends_when_no_key_line(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".trw" / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('installation_id: "test"\n', encoding="utf-8")
        _save_api_key(cfg, "trw_dk_appended")
        content = cfg.read_text(encoding="utf-8")
        assert 'platform_api_key: "trw_dk_appended"' in content
        assert 'installation_id: "test"' in content

    def test_no_existing_file(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        _save_api_key(cfg, "trw_dk_brand_new")
        content = cfg.read_text(encoding="utf-8")
        assert 'platform_api_key: "trw_dk_brand_new"' in content


class TestSaveConfigField:
    def test_creates_field_in_new_file(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        _save_config_field(cfg, "platform_org_name", "acme-corp")
        content = cfg.read_text(encoding="utf-8")
        assert 'platform_org_name: "acme-corp"' in content

    def test_updates_existing_field(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text('platform_org_name: "old-org"\n', encoding="utf-8")
        _save_config_field(cfg, "platform_org_name", "new-org")
        content = cfg.read_text(encoding="utf-8")
        assert 'platform_org_name: "new-org"' in content
        assert "old-org" not in content

    def test_appends_when_field_absent(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text('installation_id: "test"\n', encoding="utf-8")
        _save_config_field(cfg, "platform_user_email", "user@example.com")
        content = cfg.read_text(encoding="utf-8")
        assert 'platform_user_email: "user@example.com"' in content
        assert 'installation_id: "test"' in content


class TestRunAuthLoginPersistence:
    def test_saves_org_name_and_email(self, tmp_path: Path) -> None:
        """Verify run_auth_login persists org_name and user_email to config."""
        cfg = tmp_path / ".trw" / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('platform_api_key: ""\n', encoding="utf-8")

        mock_result: dict[str, object] = {
            "api_key": "trw_dk_test123",
            "org_name": "acme-corp",
            "user_email": "dev@acme.com",
            "org_id": 42,
            "organizations": [{"id": 42, "name": "acme-corp", "slug": "acme-corp"}],
        }

        with (
            patch("trw_mcp.cli.auth.device_auth_login", return_value=mock_result),
            patch("trw_mcp.cli.auth.select_organization", return_value=mock_result["organizations"][0]),
        ):
            exit_code = run_auth_login("https://api.example.com", cfg)

        assert exit_code == 0
        content = cfg.read_text(encoding="utf-8")
        assert 'platform_api_key: "trw_dk_test123"' in content
        assert 'platform_org_name: "acme-corp"' in content
        assert 'platform_user_email: "dev@acme.com"' in content

    def test_status_reads_org_and_email(self, tmp_path: Path) -> None:
        """Verify device_auth_status returns saved org_name and user_email."""
        cfg = tmp_path / ".trw" / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            'platform_api_key: "trw_dk_test123"\nplatform_org_name: "acme-corp"\nplatform_user_email: "dev@acme.com"\n',
            encoding="utf-8",
        )
        status = device_auth_status(cfg, "https://api.example.com")
        assert status["authenticated"] is True
        assert status["org_name"] == "acme-corp"
        assert status["user_email"] == "dev@acme.com"
