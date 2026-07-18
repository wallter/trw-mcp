"""PRD-SEC-005-FR03 round-2: installer prior-key resolution precedence.

The standalone installer template (``install-trw.template.py``) previously read
the platform API key from ``.trw/config.yaml`` ONLY. After SEC-005 moved the
credential into the ignored ``.trw/credentials.yaml``, both prior-key detection
(``_load_prior_config``) and ``--with-proprietary`` auto-derivation
(``_resolve_proprietary_license`` reads ``prior["api_key"]``) silently broke on
migrated installs.

These tests load the template as a module and assert the resolution precedence
env > credentials.yaml > config.yaml, and that the resolved key flows into the
prior-config dict that drives proprietary auto-derivation.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"


def _load():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("install_trw_prior_key_probe", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def installer():  # type: ignore[no-untyped-def]
    return _load()


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRW_PLATFORM_API_KEY", raising=False)


def _seed(target: Path, *, config_key: str | None = None, creds_key: str | None = None) -> None:
    trw = target / ".trw"
    trw.mkdir(parents=True, exist_ok=True)
    body = 'installation_id: "demo"\n'
    if config_key is not None:
        body += f'platform_api_key: "{config_key}"\n'
    (trw / "config.yaml").write_text(body, encoding="utf-8")
    if creds_key is not None:
        (trw / "credentials.yaml").write_text(f'platform_api_key: "{creds_key}"\n', encoding="utf-8")


def test_credentials_yaml_takes_precedence_over_config(installer, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    _seed(tmp_path, config_key="trw_dk_config", creds_key="trw_dk_creds")
    prior = installer._load_prior_config(tmp_path)
    assert prior["api_key"] == "trw_dk_creds"


def test_env_takes_precedence_over_credentials_and_config(
    installer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    _seed(tmp_path, config_key="trw_dk_config", creds_key="trw_dk_creds")
    monkeypatch.setenv("TRW_PLATFORM_API_KEY", "trw_dk_env")
    prior = installer._load_prior_config(tmp_path)
    assert prior["api_key"] == "trw_dk_env"


def test_migrated_install_resolves_from_credentials_only(installer, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """The real SEC-005 migrated state: config.yaml blanked, key in creds."""
    _seed(tmp_path, config_key="", creds_key="trw_dk_migrated")
    prior = installer._load_prior_config(tmp_path)
    assert prior["api_key"] == "trw_dk_migrated"


def test_legacy_install_falls_back_to_config(installer, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """Pre-migration install: no credentials.yaml, key still in config.yaml."""
    _seed(tmp_path, config_key="trw_dk_legacy")
    prior = installer._load_prior_config(tmp_path)
    assert prior["api_key"] == "trw_dk_legacy"


def test_no_key_anywhere_omits_api_key(installer, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    _seed(tmp_path, config_key="")
    prior = installer._load_prior_config(tmp_path)
    assert "api_key" not in prior


def test_resolved_key_drives_proprietary_auto_derive(installer, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """End-to-end: a credentials.yaml-only key reaches --with-proprietary derive.

    ``_resolve_proprietary_license`` reads ``prior_config["api_key"]``; before
    the fix it would be empty on a migrated install and raise ValueError.
    """
    _seed(tmp_path, config_key="", creds_key="trw_dk_creds_for_license")
    prior = installer._load_prior_config(tmp_path)

    captured: dict[str, str] = {}

    def _fake_fetch(backend_url: str, platform_api_key: str, timeout: int = 10) -> str:
        captured["key"] = platform_api_key
        return "LIC-XYZ"

    installer._fetch_proprietary_license = _fake_fetch

    license_key, with_prop = installer._resolve_proprietary_license(
        with_proprietary=True,
        explicit_license_key="",
        backend_url="https://api.example.com",
        prior_config=prior,
        ui=installer.UI(quiet=True),
    )
    assert license_key == "LIC-XYZ"
    assert with_prop is True
    # The credentials.yaml key — not an empty config.yaml — was used.
    assert captured["key"] == "trw_dk_creds_for_license"


def test_proprietary_derive_raises_when_no_key_on_migrated_install(installer, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """No key in any store → auto-derive raises the usage ValueError."""
    _seed(tmp_path, config_key="")
    prior = installer._load_prior_config(tmp_path)
    with pytest.raises(ValueError):
        installer._resolve_proprietary_license(
            with_proprietary=True,
            explicit_license_key="",
            backend_url="https://api.example.com",
            prior_config=prior,
            ui=installer.UI(quiet=True),
        )
