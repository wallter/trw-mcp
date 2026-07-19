"""PRD-SEC-005-FR01: auth login writes credential to credentials.yaml (0600).

The bearer credential MUST land in the ignored ``.trw/credentials.yaml`` with
mode 0600 and MUST NOT be written into the git-tracked ``.trw/config.yaml``.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.cli.auth import device_auth_status, run_auth_login
from trw_mcp.models.config._credentials import (
    credentials_path_for,
    read_key_from_file,
    write_credentials_key,
)

_LOGIN_RESULT: dict[str, object] = {
    "api_key": "trw_dk_fresh_install_key",
    "org_name": "acme-corp",
    "user_email": "dev@acme.com",
    "organizations": [{"id": 7, "name": "acme-corp", "slug": "acme-corp"}],
}


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    cfg = tmp_path / ".trw" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('installation_id: "test"\nplatform_api_key: ""\n', encoding="utf-8")
    return cfg


def test_write_credentials_creates_0600_file(tmp_path: Path) -> None:
    creds = tmp_path / ".trw" / "credentials.yaml"
    write_credentials_key(creds, "trw_dk_abc")

    assert read_key_from_file(creds) == "trw_dk_abc"
    if sys.platform != "win32":
        mode = stat.S_IMODE(os.stat(creds).st_mode)
        assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_login_writes_credentials_not_config(config_path: Path) -> None:
    """FR01: login persists the key to credentials.yaml, not config.yaml."""
    with (
        patch("trw_mcp.cli.auth.device_auth_login", return_value=_LOGIN_RESULT),
    ):
        exit_code = run_auth_login("https://api.example.com", config_path)

    assert exit_code == 0
    creds = credentials_path_for(config_path)
    assert creds.is_file()
    assert read_key_from_file(creds) == "trw_dk_fresh_install_key"

    # The credential MUST NOT be written into the git-tracked config.yaml.
    config_text = config_path.read_text(encoding="utf-8")
    assert "trw_dk_fresh_install_key" not in config_text


def test_login_credentials_file_is_0600(config_path: Path) -> None:
    with (
        patch("trw_mcp.cli.auth.device_auth_login", return_value=_LOGIN_RESULT),
    ):
        run_auth_login("https://api.example.com", config_path)

    creds = credentials_path_for(config_path)
    if sys.platform != "win32":
        mode = stat.S_IMODE(os.stat(creds).st_mode)
        assert mode == 0o600


def test_login_keeps_non_secret_metadata_in_config(config_path: Path) -> None:
    """Org/email metadata (non-secret) still lands in config.yaml."""
    with (
        patch("trw_mcp.cli.auth.device_auth_login", return_value=_LOGIN_RESULT),
    ):
        run_auth_login("https://api.example.com", config_path)

    config_text = config_path.read_text(encoding="utf-8")
    assert 'platform_org_name: "acme-corp"' in config_text
    assert 'platform_user_email: "dev@acme.com"' in config_text


def test_status_reads_key_from_credentials_file(config_path: Path) -> None:
    """`auth status` resolves the key from credentials.yaml (post-login)."""
    write_credentials_key(credentials_path_for(config_path), "trw_dk_in_creds")

    status = device_auth_status(config_path, "https://api.example.com")

    assert status["authenticated"] is True
    assert str(status["key_prefix"]).startswith("trw_dk_in_")


def test_status_never_reads_key_from_config_yaml(tmp_path: Path) -> None:
    """A key left in the git-tracked config.yaml must NOT authenticate.

    Post-SEC-005 the config.yaml fallback is removed: an un-migrated legacy key
    is not a resolution source. `auth status` reports unauthenticated until the
    key is migrated into credentials.yaml (by `trw-mcp update-project`).
    """
    cfg = tmp_path / ".trw" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('platform_api_key: "trw_dk_legacy_only"\n', encoding="utf-8")

    status = device_auth_status(cfg, "https://api.example.com")

    assert status["authenticated"] is False
