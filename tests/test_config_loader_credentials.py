"""PRD-SEC-005 FR03: platform-credential loader precedence (credentials-only).

The platform API key resolves with precedence
``TRW_PLATFORM_API_KEY`` env > ``TRW_API_KEY`` env > ``.trw/credentials.yaml``.

The git-tracked ``.trw/config.yaml`` is NEVER a resolution source (the
credential is a secret and must not live in a tracked file). A legacy tracked
key is migrated into ``credentials.yaml`` by ``trw-mcp update-project``; the
loader has no config.yaml fallback and emits no deprecation path.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog

from trw_mcp.models.config import get_config, reload_config


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
    monkeypatch.delenv("TRW_API_KEY", raising=False)
    monkeypatch.delenv("TRW_CONFIG_STRICT", raising=False)
    structlog.reset_defaults()
    reload_config()
    yield
    reload_config()


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _config(project_dir: Path) -> Path:
    return project_dir / ".trw" / "config.yaml"


def _credentials_file(project_dir: Path) -> Path:
    return project_dir / ".trw" / "credentials.yaml"


def test_config_yaml_is_not_a_credential_source(project_dir: Path) -> None:
    """A key still present in the git-tracked config.yaml must NOT resolve.

    This is the core SEC-005 hardening: the deprecated config.yaml fallback is
    removed, so a tracked secret can never leak into the runtime config.
    """
    _write(_config(project_dir), 'platform_api_key: "trw_dk_legacy"\n')

    cfg = get_config()

    assert cfg.platform_api_key.get_secret_value() == ""


def test_credentials_yaml_resolves_key(project_dir: Path) -> None:
    """The 0600 credentials.yaml is the on-disk source of truth."""
    _write(_credentials_file(project_dir), 'platform_api_key: "trw_dk_creds"\n')

    cfg = get_config()

    assert cfg.platform_api_key.get_secret_value() == "trw_dk_creds"


def test_stray_config_key_never_overrides_credentials(project_dir: Path) -> None:
    """A stray config.yaml key is ignored; credentials.yaml always wins."""
    _write(_config(project_dir), 'platform_api_key: "trw_dk_config"\n')
    _write(_credentials_file(project_dir), 'platform_api_key: "trw_dk_creds"\n')

    cfg = get_config()

    assert cfg.platform_api_key.get_secret_value() == "trw_dk_creds"


def test_env_var_overrides_credentials(project_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TRW_PLATFORM_API_KEY overrides credentials.yaml (FR03)."""
    _write(_credentials_file(project_dir), 'platform_api_key: "trw_dk_creds"\n')
    monkeypatch.setenv("TRW_PLATFORM_API_KEY", "trw_dk_env")

    cfg = get_config()

    assert cfg.platform_api_key.get_secret_value() == "trw_dk_env"


def test_trw_api_key_env_alias_resolves(project_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TRW_API_KEY is accepted as an alias for the enterprise env path."""
    _write(_config(project_dir), "installation_id: x\n")
    monkeypatch.setenv("TRW_API_KEY", "trw_dk_alias_env")

    cfg = get_config()

    assert cfg.platform_api_key.get_secret_value() == "trw_dk_alias_env"


def test_platform_env_wins_over_api_key_alias(project_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When both env vars are set, TRW_PLATFORM_API_KEY takes precedence."""
    monkeypatch.setenv("TRW_PLATFORM_API_KEY", "trw_dk_platform")
    monkeypatch.setenv("TRW_API_KEY", "trw_dk_alias")

    cfg = get_config()

    assert cfg.platform_api_key.get_secret_value() == "trw_dk_platform"


def test_env_var_resolves_with_no_files(project_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Enterprise path: env var works with zero on-disk key (US-003)."""
    _write(_config(project_dir), "installation_id: x\n")
    monkeypatch.setenv("TRW_PLATFORM_API_KEY", "trw_dk_env_only")

    cfg = get_config()

    assert cfg.platform_api_key.get_secret_value() == "trw_dk_env_only"


def test_missing_all_sources_yields_empty_key(project_dir: Path) -> None:
    """No source ⇒ empty key, no crash (negative/fallback test)."""
    _write(_config(project_dir), "installation_id: x\n")

    cfg = get_config()

    assert cfg.platform_api_key.get_secret_value() == ""
