"""PRD-CORE-185 FR09: installer auto-detection + provisioning of the user scope.

The installer auto-detects whether a machine-local user-scope is warranted by
probing common home / XDG / agent-harness paths (``~/.claude``, ``~/.codex``,
``~/.config/*``, ``~/.trw``, XDG base dirs). When a sensible setup exists it
provisions the user-scope store location + seeds ``~/.trw/config.yaml`` (the
FR04 machine layer) NON-destructively. On a bare box it provisions nothing and
TRW stays project-only with zero config. No network call is made.

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
# Detection
# --------------------------------------------------------------------------- #


def test_detect_user_scope_finds_claude(installer, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    (_home(tmp_path) / ".claude").mkdir(parents=True)
    assert installer._detect_user_scope() is True


def test_detect_user_scope_finds_codex(installer, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    (_home(tmp_path) / ".codex").mkdir(parents=True)
    assert installer._detect_user_scope() is True


def test_detect_user_scope_finds_xdg_config(  # type: ignore[no-untyped-def]
    installer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    xdg = tmp_path / "xdg-config"
    (xdg / "someharness").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    assert installer._detect_user_scope() is True


def test_detect_user_scope_bare_box_false(installer, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """An empty home with no harness markers -> no user scope warranted."""
    assert installer._detect_user_scope() is False


# --------------------------------------------------------------------------- #
# Provisioning
# --------------------------------------------------------------------------- #


def test_provision_creates_config_and_store_dir(installer, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    (_home(tmp_path) / ".claude").mkdir(parents=True)
    provisioned = installer._provision_user_scope()
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

    installer._provision_user_scope()

    text = cfg.read_text(encoding="utf-8")
    # Existing keys are NOT clobbered.
    assert "existing_key: keepme" in text
    assert "user_tier_enabled: false" in text  # pre-existing value preserved
    # We did not append a duplicate key.
    assert text.count("user_tier_enabled:") == 1


def test_provision_bare_box_is_noop(installer, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """No harness markers -> provision nothing (project-only, zero config)."""
    provisioned = installer._provision_user_scope()
    assert provisioned is False
    assert not (_home(tmp_path) / ".trw" / "config.yaml").exists()
    assert not (_home(tmp_path) / ".trw" / "memory").exists()


def test_provision_does_not_touch_project_data(installer, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    (_home(tmp_path) / ".claude").mkdir(parents=True)
    project = tmp_path / "proj"
    (project / ".trw").mkdir(parents=True)
    sentinel = project / ".trw" / "config.yaml"
    sentinel.write_text("installation_id: myproj\n", encoding="utf-8")

    installer._provision_user_scope()

    # The project config is untouched.
    assert sentinel.read_text(encoding="utf-8") == "installation_id: myproj\n"
