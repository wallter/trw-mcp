"""PRD-SEC-005 FR03/FR04: platform-credential loader precedence + deprecation.

The platform API key must resolve with precedence
``TRW_PLATFORM_API_KEY`` env > ``.trw/credentials.yaml`` >
(backward-compat) ``.trw/config.yaml``, with a one-shot deprecation warning
emitted only when the key is sourced from the deprecated config.yaml. The
config.yaml fallback must keep working (no exception).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog
from structlog.testing import capture_logs

from trw_mcp.models.config import _credentials, get_config, reload_config


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    (tmp_path / "proj" / ".git").mkdir(parents=True)
    (tmp_path / "proj" / ".trw").mkdir(parents=True)
    return tmp_path / "proj"


@pytest.fixture(autouse=True)
def _isolate(project_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: empty_home))
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project_dir)
    monkeypatch.delenv("TRW_PLATFORM_API_KEY", raising=False)
    monkeypatch.delenv("TRW_CONFIG_STRICT", raising=False)
    structlog.reset_defaults()
    _credentials.reset_deprecation_state()
    reload_config()
    yield
    reload_config()
    _credentials.reset_deprecation_state()


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _config(project_dir: Path) -> Path:
    return project_dir / ".trw" / "config.yaml"


def _credentials_file(project_dir: Path) -> Path:
    return project_dir / ".trw" / "credentials.yaml"


def test_config_yaml_fallback_resolves_key(project_dir: Path) -> None:
    """A config.yaml-only install (THIS repo style) still resolves the key."""
    _write(_config(project_dir), 'platform_api_key: "trw_dk_legacy"\n')

    cfg = get_config()

    assert cfg.platform_api_key.get_secret_value() == "trw_dk_legacy"


def test_config_yaml_fallback_emits_deprecation_warning(project_dir: Path) -> None:
    """The config.yaml fallback emits a one-shot deprecation warning (FR04)."""
    _write(_config(project_dir), 'platform_api_key: "trw_dk_legacy"\n')

    with capture_logs() as logs:
        get_config()

    events = [r["event"] for r in logs]
    assert events.count("platform_api_key_from_deprecated_config") == 1


def test_deprecation_warning_emitted_once_per_process(project_dir: Path) -> None:
    """FR04: at most one deprecation warning per process across resolutions."""
    _write(_config(project_dir), 'platform_api_key: "trw_dk_legacy"\n')

    with capture_logs() as logs:
        _credentials.resolve_platform_api_key(_config(project_dir), config_key="trw_dk_legacy")
        _credentials.resolve_platform_api_key(_config(project_dir), config_key="trw_dk_legacy")

    events = [r["event"] for r in logs]
    assert events.count("platform_api_key_from_deprecated_config") == 1


def test_credentials_yaml_takes_precedence_over_config(project_dir: Path) -> None:
    """credentials.yaml wins over config.yaml (FR03)."""
    _write(_config(project_dir), 'platform_api_key: "trw_dk_config"\n')
    _write(_credentials_file(project_dir), 'platform_api_key: "trw_dk_creds"\n')

    cfg = get_config()

    assert cfg.platform_api_key.get_secret_value() == "trw_dk_creds"


def test_credentials_yaml_does_not_emit_deprecation(project_dir: Path) -> None:
    """No deprecation warning when the key comes from credentials.yaml."""
    _write(_credentials_file(project_dir), 'platform_api_key: "trw_dk_creds"\n')

    with capture_logs() as logs:
        get_config()

    events = [r["event"] for r in logs]
    assert "platform_api_key_from_deprecated_config" not in events


def test_env_var_overrides_both_files(project_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TRW_PLATFORM_API_KEY overrides credentials.yaml and config.yaml (FR03)."""
    _write(_config(project_dir), 'platform_api_key: "trw_dk_config"\n')
    _write(_credentials_file(project_dir), 'platform_api_key: "trw_dk_creds"\n')
    monkeypatch.setenv("TRW_PLATFORM_API_KEY", "trw_dk_env")

    cfg = get_config()

    assert cfg.platform_api_key.get_secret_value() == "trw_dk_env"


def test_env_var_resolves_with_no_files(project_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Enterprise path: env var works with zero on-disk key (US-003)."""
    _write(_config(project_dir), "installation_id: x\n")
    monkeypatch.setenv("TRW_PLATFORM_API_KEY", "trw_dk_env_only")

    cfg = get_config()

    assert cfg.platform_api_key.get_secret_value() == "trw_dk_env_only"


def test_missing_both_sources_yields_empty_key(project_dir: Path) -> None:
    """No source ⇒ empty key, no crash (negative/fallback test)."""
    _write(_config(project_dir), "installation_id: x\n")

    cfg = get_config()

    assert cfg.platform_api_key.get_secret_value() == ""


def test_env_precedence_does_not_emit_deprecation(project_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Env override short-circuits before the deprecated config.yaml path."""
    _write(_config(project_dir), 'platform_api_key: "trw_dk_config"\n')
    monkeypatch.setenv("TRW_PLATFORM_API_KEY", "trw_dk_env")

    with capture_logs() as logs:
        get_config()

    events = [r["event"] for r in logs]
    assert "platform_api_key_from_deprecated_config" not in events
