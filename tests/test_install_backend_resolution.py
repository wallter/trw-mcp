"""install-trw backend resolution: pip vs uv-managed CPython (no pip).

Proves `resolve_install_backend` / `build_install_cmd` handle a uv-managed
Python that lacks `python -m pip`: it bootstraps via ensurepip when possible,
falls back to `uv pip install`, and honors the TRW_INSTALL_BACKEND override.
Regression for the pip path is covered by test_install_trw_no_deps_regression.
"""

from __future__ import annotations

import pytest

from tests._install_trw_pip_target_contract_support import (
    _INSTALLER_TEMPLATE,
    _load_installer_module,
)

PY = "/fake/uv/python3"
UV = "/fake/bin/uv"


class _StubUI:
    def __init__(self) -> None:
        self.warns: list[str] = []
        self.errors: list[str] = []

    def step_warn(self, msg: str) -> None:
        self.warns.append(msg)

    def error(self, msg: str) -> None:
        self.errors.append(msg)


@pytest.fixture
def mod():
    m = _load_installer_module(_INSTALLER_TEMPLATE)
    m._INSTALL_BACKEND = None  # reset the per-run cache
    return m


def _fake_run_quiet(*, pip_version: bool, ensurepip: bool):
    """Build a _run_quiet stand-in keyed on the probe command."""

    def run_quiet(cmd, timeout=120):
        if "--version" in cmd and "pip" in cmd:
            return pip_version
        if "ensurepip" in cmd:
            return ensurepip
        return False

    return run_quiet


def test_pip_backend_when_pip_present(mod, monkeypatch):
    monkeypatch.delenv("TRW_INSTALL_BACKEND", raising=False)
    monkeypatch.setattr(mod, "_run_quiet", _fake_run_quiet(pip_version=True, ensurepip=False))
    kind, prefix = mod.resolve_install_backend(PY, _StubUI())
    assert kind == "pip"
    assert prefix == [PY, "-B", "-m", "pip", "install"]


def test_uv_fallback_when_pip_absent_and_ensurepip_fails(mod, monkeypatch):
    monkeypatch.delenv("TRW_INSTALL_BACKEND", raising=False)
    monkeypatch.setenv("TRW_UV_BIN", UV)
    monkeypatch.setattr(mod, "_run_quiet", _fake_run_quiet(pip_version=False, ensurepip=False))
    ui = _StubUI()
    kind, prefix = mod.resolve_install_backend(PY, ui)
    assert kind == "uv"
    assert prefix == [UV, "pip", "install", "--python", PY]
    assert any("uv pip backend" in w for w in ui.warns)


def test_ensurepip_bootstrap_recovers_pip(mod, monkeypatch):
    monkeypatch.delenv("TRW_INSTALL_BACKEND", raising=False)
    monkeypatch.setenv("TRW_UV_BIN", UV)
    # pip absent on first probe, but ensurepip succeeds AND the post-bootstrap
    # pip --version probe then succeeds → pip backend (uv NOT used).
    calls = {"pip_version": 0}

    def run_quiet(cmd, timeout=120):
        if "--version" in cmd and "pip" in cmd:
            calls["pip_version"] += 1
            return calls["pip_version"] > 1  # False first, True after ensurepip
        if "ensurepip" in cmd:
            return True
        return False

    monkeypatch.setattr(mod, "_run_quiet", run_quiet)
    kind, prefix = mod.resolve_install_backend(PY, _StubUI())
    assert kind == "pip"
    assert prefix == [PY, "-B", "-m", "pip", "install"]


def test_explicit_uv_override(mod, monkeypatch):
    monkeypatch.setenv("TRW_INSTALL_BACKEND", "uv")
    monkeypatch.setenv("TRW_UV_BIN", UV)
    # _run_quiet must never be consulted on an explicit override.
    monkeypatch.setattr(mod, "_run_quiet", _fake_run_quiet(pip_version=True, ensurepip=True))
    kind, prefix = mod.resolve_install_backend(PY, _StubUI())
    assert kind == "uv"
    assert prefix == [UV, "pip", "install", "--python", PY]


def test_no_backend_exits_actionably(mod, monkeypatch):
    monkeypatch.delenv("TRW_INSTALL_BACKEND", raising=False)
    monkeypatch.delenv("TRW_UV_BIN", raising=False)
    monkeypatch.setattr(mod, "shutil", mod.shutil)
    monkeypatch.setattr(mod.shutil, "which", lambda _name: None)  # no uv on PATH
    monkeypatch.setattr(mod, "_run_quiet", _fake_run_quiet(pip_version=False, ensurepip=False))
    ui = _StubUI()
    with pytest.raises(SystemExit):
        mod.resolve_install_backend(PY, ui)
    assert any("uv" in e for e in ui.errors)  # guidance mentions uv


def test_build_install_cmd_flag_translation(mod):
    # pip backend → pip flags
    mod._INSTALL_BACKEND = ("pip", [PY, "-B", "-m", "pip", "install"])
    pip_cmd = mod.build_install_cmd(
        PY,
        _StubUI(),
        ["/w/pkg.whl"],
        target_dir="/t",
        find_links="/w",
        no_cache=True,
        no_warn_script_location=True,
    )
    assert "--no-cache-dir" in pip_cmd and "--no-cache" not in pip_cmd
    assert "--no-warn-script-location" in pip_cmd
    assert pip_cmd[-1] == "/w/pkg.whl"
    assert pip_cmd[pip_cmd.index("--target") + 1] == "/t"

    # uv backend → uv flags; pip-only flag dropped
    mod._INSTALL_BACKEND = ("uv", [UV, "pip", "install", "--python", PY])
    uv_cmd = mod.build_install_cmd(
        PY,
        _StubUI(),
        ["/w/pkg.whl"],
        target_dir="/t",
        find_links="/w",
        no_cache=True,
        no_warn_script_location=True,
    )
    assert "--no-cache" in uv_cmd and "--no-cache-dir" not in uv_cmd
    assert "--no-warn-script-location" not in uv_cmd
    assert uv_cmd[:5] == [UV, "pip", "install", "--python", PY]
    assert uv_cmd[-1] == "/w/pkg.whl"
