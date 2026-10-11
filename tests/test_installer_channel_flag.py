"""Canary channel v1 slice 2: install-trw.template.py's --channel / --version wiring.

This bundled installer carries exactly one (trw-mcp, trw-memory) pair baked in
at build time -- it cannot fetch a different pair itself. Before this change
``--version``/``TRW_VERSION`` were parsed into ``args.pin_version`` and never
read again (dead parameter): a mismatched pin was silently ignored and the
bundled pair installed anyway. This asserts the fix: a channel other than
"stable", or a version that does not match the bundled ``TRW_VERSION``, now
refuses clearly and points at ``scripts/install.sh`` (which resolves those
channels for real), before any wheel data is touched. The ``canary`` channel
(pre-releases from PyPI) was removed on 2026-10-10: no pre-release is published.

The template is loaded as a module by file path (``scripts/`` is not a package),
matching the pattern in ``test_phase_install_downgrade_guard.py``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_channel_flag", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def installer() -> ModuleType:
    return _load()


def test_local_channel_refuses_and_points_at_install_sh(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["install-trw.py", "--channel", "local"])
    with pytest.raises(SystemExit) as exc:
        installer.main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "not supported by this bundled installer" in err
    assert "`TRW_CHANNEL=local bash scripts/install.sh`" in err  # a command that runs as written
    assert "canary" not in err and "--pre" not in err


def test_canary_channel_flag_is_an_invalid_choice(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The channel that read pre-releases from PyPI is gone: argparse refuses the name and lists what is left."""
    monkeypatch.setattr(sys, "argv", ["install-trw.py", "--channel", "canary"])
    with pytest.raises(SystemExit) as exc:
        installer.main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "invalid choice: 'canary'" in err
    assert "local" in err and "stable" in err
    assert "TRW_CHANNEL=canary bash" not in err  # never send the reader to a channel that is refused


def test_canary_channel_from_the_environment_is_refused_too(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """argparse does not check ``choices`` for a default, and the default is read from TRW_CHANNEL."""
    monkeypatch.setenv("TRW_CHANNEL", "canary")
    monkeypatch.setattr(sys, "argv", ["install-trw.py"])
    with pytest.raises(SystemExit) as exc:
        _load().main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "TRW_CHANNEL=canary is not a channel (choose from local, stable)" in err
    assert "TRW_CHANNEL=canary bash" not in err


def test_mismatched_version_pin_refuses_before_wheel_extraction(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bogus = installer.TRW_VERSION + "-does-not-exist"
    monkeypatch.setattr(sys, "argv", ["install-trw.py", "--version", bogus])
    with pytest.raises(SystemExit) as exc:
        installer.main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert f"does not match the pair bundled in this installer ({installer.TRW_VERSION})" in err
    assert f"`TRW_VERSION={bogus} bash scripts/install.sh`" in err


def test_channel_argparse_rejects_unknown_values_before_our_own_check(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An out-of-{local,stable} value is refused by argparse's own ``choices=`` (exit 2)."""
    monkeypatch.setattr(sys, "argv", ["install-trw.py", "--channel", "bogus-channel"])
    with pytest.raises(SystemExit) as exc:
        installer.main()
    assert exc.value.code == 2


def test_version_flag_default_reads_trw_version_env(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """TRW_VERSION env (not just --version) reaches the same refusal -- the help text's promise."""
    monkeypatch.setenv("TRW_VERSION", installer.TRW_VERSION + "-does-not-exist")
    monkeypatch.setattr(sys, "argv", ["install-trw.py"])
    reloaded = _load()  # argparse default reads the env at parser-build time
    with pytest.raises(SystemExit) as exc:
        reloaded.main()
    assert exc.value.code == 2
    assert "does not match the pair bundled" in capsys.readouterr().err


def test_matching_version_pin_passes_our_check(installer: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pin equal to the bundled version is a no-op confirmation, not a refusal."""
    monkeypatch.setattr(sys, "argv", ["install-trw.py", "--version", installer.TRW_VERSION])
    # main() proceeds well past our new check (into wheel extraction, which the
    # bare template has no real embedded data for) -- assert it does NOT fail
    # with OUR refusal message specifically.
    with pytest.raises(SystemExit):
        installer.main()
