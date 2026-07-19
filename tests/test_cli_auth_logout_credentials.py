"""PRD-SEC-005 round-2: ``auth logout`` must remove the credentials.yaml key.

Before this fix, ``device_auth_logout`` only blanked ``config.yaml``'s
``platform_api_key`` — but post-SEC-005 the bearer credential lives in
``.trw/credentials.yaml``. Logout left the credential in place, so
``auth status`` (and the runtime resolver) still reported authenticated.

These tests assert that after logout NO source resolves a key
(env > credentials.yaml), via ``resolve_platform_api_key``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.cli.auth import device_auth_logout, device_auth_status
from trw_mcp.models.config._credentials import (
    credentials_path_for,
    read_key_from_file,
    resolve_platform_api_key,
    write_credentials_key,
)


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure the enterprise env vars never mask on-disk resolution in tests."""
    monkeypatch.delenv("TRW_PLATFORM_API_KEY", raising=False)
    monkeypatch.delenv("TRW_API_KEY", raising=False)


def test_logout_removes_credentials_yaml_key(tmp_path: Path) -> None:
    cfg = tmp_path / ".trw" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('installation_id: "test"\n', encoding="utf-8")
    creds = credentials_path_for(cfg)
    write_credentials_key(creds, "trw_dk_secret123")

    # Sanity: a key resolves before logout.
    assert resolve_platform_api_key(cfg) == "trw_dk_secret123"

    removed = device_auth_logout(cfg)

    assert removed is True
    # Credential no longer resolvable from any source.
    assert resolve_platform_api_key(cfg) == ""
    assert read_key_from_file(creds) == ""


def test_logout_clears_both_credentials_and_config_fallback(tmp_path: Path) -> None:
    cfg = tmp_path / ".trw" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    # Legacy install: key still in config.yaml AND a migrated credentials.yaml.
    cfg.write_text('platform_api_key: "trw_dk_legacy"\n', encoding="utf-8")
    creds = credentials_path_for(cfg)
    write_credentials_key(creds, "trw_dk_migrated")

    removed = device_auth_logout(cfg)

    assert removed is True
    assert read_key_from_file(creds) == ""
    assert read_key_from_file(cfg) == ""
    # Config field is blanked, not deleted (keeps file structure valid).
    assert 'platform_api_key: ""' in cfg.read_text(encoding="utf-8")


def test_logout_clears_config_only_legacy_install(tmp_path: Path) -> None:
    cfg = tmp_path / ".trw" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('platform_api_key: "trw_dk_legacy"\n', encoding="utf-8")

    removed = device_auth_logout(cfg)

    assert removed is True
    assert read_key_from_file(cfg) == ""


def test_logout_idempotent_when_nothing_present(tmp_path: Path) -> None:
    cfg = tmp_path / ".trw" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('installation_id: "test"\n', encoding="utf-8")

    removed = device_auth_logout(cfg)

    assert removed is False


def test_logout_no_config_dir_is_noop(tmp_path: Path) -> None:
    removed = device_auth_logout(tmp_path / "nonexistent" / "config.yaml")
    assert removed is False


def test_status_unauthenticated_after_logout(tmp_path: Path) -> None:
    cfg = tmp_path / ".trw" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('platform_org_name: "acme"\n', encoding="utf-8")
    write_credentials_key(credentials_path_for(cfg), "trw_dk_secret")

    assert device_auth_status(cfg, "https://api.example.com")["authenticated"] is True
    device_auth_logout(cfg)
    assert device_auth_status(cfg, "https://api.example.com")["authenticated"] is False
