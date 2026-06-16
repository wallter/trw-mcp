"""PRD-SEC-005-FR05: update-project credential migration (idempotent).

A tracked ``config.yaml`` key is moved into ``credentials.yaml`` (mode 0600)
and blanked in config.yaml; repeated runs are a no-op.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from trw_mcp.models.config._credentials import (
    credentials_path_for,
    migrate_config_key,
    migrate_for_update_project,
    read_key_from_file,
)


def _config(tmp_path: Path) -> Path:
    cfg = tmp_path / ".trw" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    return cfg


def test_migration_moves_key_and_blanks_config(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.write_text('installation_id: "x"\nplatform_api_key: "trw_dk_tracked"\n', encoding="utf-8")

    migrated = migrate_config_key(cfg)

    assert migrated is True
    creds = credentials_path_for(cfg)
    assert read_key_from_file(creds) == "trw_dk_tracked"
    # config.yaml field is blanked, not removed.
    config_text = cfg.read_text(encoding="utf-8")
    assert 'platform_api_key: ""' in config_text
    assert "trw_dk_tracked" not in config_text


def test_migrated_credentials_file_is_0600(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.write_text('platform_api_key: "trw_dk_tracked"\n', encoding="utf-8")

    migrate_config_key(cfg)

    if sys.platform != "win32":
        creds = credentials_path_for(cfg)
        mode = stat.S_IMODE(os.stat(creds).st_mode)
        assert mode == 0o600


def test_migration_is_idempotent(tmp_path: Path) -> None:
    """A second run is a no-op (FR05 / US-004)."""
    cfg = _config(tmp_path)
    cfg.write_text('platform_api_key: "trw_dk_tracked"\n', encoding="utf-8")

    assert migrate_config_key(cfg) is True
    # Second run: nothing left to migrate.
    assert migrate_config_key(cfg) is False
    assert read_key_from_file(credentials_path_for(cfg)) == "trw_dk_tracked"


def test_migration_noop_when_no_key(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.write_text('installation_id: "x"\n', encoding="utf-8")

    assert migrate_config_key(cfg) is False
    assert not credentials_path_for(cfg).exists()


def test_migration_noop_when_key_blank(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.write_text('platform_api_key: ""\n', encoding="utf-8")

    assert migrate_config_key(cfg) is False


def test_update_project_helper_records_notes(tmp_path: Path) -> None:
    """The update-project wrapper records updated/warning notes on success."""
    cfg = _config(tmp_path)
    cfg.write_text('platform_api_key: "trw_dk_tracked"\n', encoding="utf-8")
    result: dict[str, list[str]] = {"updated": [], "warnings": [], "errors": []}

    migrate_for_update_project(cfg, result)

    assert any("credentials.yaml" in u for u in result["updated"])
    assert any("ROTATE" in w for w in result["warnings"])


def test_update_project_helper_idempotent_no_notes(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.write_text('platform_api_key: "trw_dk_tracked"\n', encoding="utf-8")
    first: dict[str, list[str]] = {"updated": [], "warnings": [], "errors": []}
    migrate_for_update_project(cfg, first)

    second: dict[str, list[str]] = {"updated": [], "warnings": [], "errors": []}
    migrate_for_update_project(cfg, second)

    assert second["updated"] == []
    assert second["warnings"] == []


def test_update_project_helper_noop_missing_config(tmp_path: Path) -> None:
    cfg = tmp_path / ".trw" / "config.yaml"
    result: dict[str, list[str]] = {"updated": [], "warnings": [], "errors": []}

    migrate_for_update_project(cfg, result)

    assert result == {"updated": [], "warnings": [], "errors": []}
