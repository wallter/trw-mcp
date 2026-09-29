"""PRD-SEC-005-FR05: update-project credential migration (idempotent).

A tracked ``config.yaml`` key is moved into ``credentials.yaml`` (mode 0600)
and blanked in config.yaml; repeated runs are a no-op.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from tests._structlog_capture import captured_structlog  # noqa: F401
from trw_mcp.models.config._credentials import (
    credentials_path_for,
    migrate_config_key,
    migrate_for_update_project,
    read_key_from_file,
)
from trw_mcp.models.config._loader import resolve_config_overrides


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


def test_backend_api_key_dropped_from_tracked_config(
    tmp_path: Path, captured_structlog: list[dict[str, object]]
) -> None:
    """PRD-SEC-022-FR03: a tracked config.yaml is never a credential source for
    backend_api_key -- resolve_config_overrides (the function get_config's
    cascade builds on) must drop a non-empty value, exactly as it already
    drops platform_api_key. A field-name-only warning is emitted; the value
    itself is never logged."""
    cfg = _config(tmp_path)
    # Not a real secret: a placeholder built to satisfy the field's shape,
    # never assembled to look like a live trw_ key (NFR01).
    fixture_value = "placeholder-not-a-real-secret-0123456789"
    cfg.write_text(f'installation_id: "x"\nbackend_api_key: "{fixture_value}"\n', encoding="utf-8")

    merged = resolve_config_overrides(cfg)

    assert "backend_api_key" not in merged
    warnings = [e for e in captured_structlog if e.get("event") == "tracked_config_secret_dropped"]
    assert len(warnings) == 1
    assert warnings[0]["field"] == "backend_api_key"
    for event in captured_structlog:
        assert fixture_value not in repr(event)


def test_platform_api_key_drop_regression(tmp_path: Path) -> None:
    """Locks the existing platform_api_key drop (no direct test today) so a
    future change to resolve_config_overrides cannot silently reopen it."""
    cfg = _config(tmp_path)
    fixture_value = "placeholder-not-a-real-secret-9876543210"
    cfg.write_text(f'installation_id: "x"\nplatform_api_key: "{fixture_value}"\n', encoding="utf-8")

    merged = resolve_config_overrides(cfg)

    # The tracked value is dropped before resolve_platform_api_key re-derives
    # it from env/credentials; with neither set here, the key is absent.
    assert merged.get("platform_api_key", "") != fixture_value
