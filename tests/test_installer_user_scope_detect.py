"""Installer user-scope detection and consent-gated provisioning.

Detection remains available for prompts and diagnostics, but provisioning the
machine-local ``~/.trw`` tier is now consent-gated (PRD-SEC-006-FR06). The
provisioner is non-destructive, writes only user-scope files, and performs no
network calls.

These tests load the template installer as a module and exercise the two new
helpers directly with ``HOME`` / XDG pointed at a tmp dir.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"


def _load():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("install_trw_user_scope_probe", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def installer():  # type: ignore[no-untyped-def]
    return _load()


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("TRW_USER_DIR", raising=False)


def _home(tmp_path: Path) -> Path:
    return tmp_path / "home"


# --------------------------------------------------------------------------- #
# Provisioning
# --------------------------------------------------------------------------- #


def test_provision_creates_config_and_store_dir(installer, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    (_home(tmp_path) / ".claude").mkdir(parents=True)
    provisioned = installer._provision_user_scope(consented=True)
    assert provisioned is True

    cfg = _home(tmp_path) / ".trw" / "config.yaml"
    assert cfg.is_file()
    text = cfg.read_text(encoding="utf-8")
    assert "user_tier_enabled" in text
    # The machine-local memory store parent dir exists.
    assert (_home(tmp_path) / ".trw" / "memory").is_dir()


def test_provision_non_destructive_preserves_existing_keys(  # type: ignore[no-untyped-def]
    installer, tmp_path: Path
) -> None:
    (_home(tmp_path) / ".claude").mkdir(parents=True)
    cfg = _home(tmp_path) / ".trw" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("user_tier_enabled: false\nexisting_key: keepme\n", encoding="utf-8")

    installer._provision_user_scope(consented=True)

    text = cfg.read_text(encoding="utf-8")
    # Existing keys are NOT clobbered.
    assert "existing_key: keepme" in text
    assert "user_tier_enabled: false" in text  # pre-existing value preserved
    # We did not append a duplicate key.
    assert text.count("user_tier_enabled:") == 1


def test_provision_without_consent_is_noop(installer, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """No consent -> provision nothing (project-only, zero config)."""
    (_home(tmp_path) / ".claude").mkdir(parents=True)
    provisioned = installer._provision_user_scope(consented=False)
    assert provisioned is False
    assert not (_home(tmp_path) / ".trw" / "config.yaml").exists()
    assert not (_home(tmp_path) / ".trw" / "memory").exists()


def test_provision_does_not_touch_project_data(installer, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    (_home(tmp_path) / ".claude").mkdir(parents=True)
    project = tmp_path / "proj"
    (project / ".trw").mkdir(parents=True)
    sentinel = project / ".trw" / "config.yaml"
    sentinel.write_text("installation_id: myproj\n", encoding="utf-8")

    installer._provision_user_scope(consented=True)

    # The project config is untouched.
    assert sentinel.read_text(encoding="utf-8") == "installation_id: myproj\n"
