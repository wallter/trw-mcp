"""INC-144: a host whose clock is UTC passes the installer's index preflight.

Containers, CI runners and most servers keep a UTC clock, and the preflight
refused offset 0, so a fresh agent host could not install. Operator decision
(2026-10-01): offset 0 passes the clock check; other non-US offsets are still
refused, and the network check is unchanged. A refusal now names the clock
offset it saw and the ``TRW_SKIP_INDEX_PREFLIGHT=1`` bypass.

Only the TEMPLATE is loaded; ``dist/install-trw.py`` is rebuilt from it.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

from tests._install_trw_pip_target_contract_support import _load_installer_module

_TEMPLATE = Path(__file__).resolve().parents[1] / "scripts" / "install-trw.template.py"


@pytest.fixture(scope="module")
def installer() -> ModuleType:
    return _load_installer_module(_TEMPLATE)


class _UI:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)


def _preflight(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, *, offset: int, org: str = "AS7922 Comcast"
) -> _UI:
    monkeypatch.delenv("TRW_SKIP_INDEX_PREFLIGHT", raising=False)
    monkeypatch.setattr(installer, "_probe_edge", lambda: {"loc": "US", "warp": "off", "org": org})
    monkeypatch.setattr(installer, "_local_utc_offset", lambda: offset)
    ui = _UI()
    installer.index_preflight(ui)
    return ui


def test_a_utc_clock_passes_the_preflight(installer: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    ui = _preflight(installer, monkeypatch, offset=0)

    assert ui.errors == []


def test_a_us_clock_still_passes(installer: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _preflight(installer, monkeypatch, offset=-7).errors == []


def test_a_non_us_offset_is_refused_naming_the_offset_and_the_bypass(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui = _UI()
    monkeypatch.delenv("TRW_SKIP_INDEX_PREFLIGHT", raising=False)
    monkeypatch.setattr(installer, "_probe_edge", lambda: {"loc": "US", "warp": "off", "org": "AS7922 Comcast"})
    monkeypatch.setattr(installer, "_local_utc_offset", lambda: 9)
    with pytest.raises(SystemExit):
        installer.index_preflight(ui)

    text = " ".join(ui.errors)
    assert "clock offset UTC+9" in text
    assert "TRW_SKIP_INDEX_PREFLIGHT=1" in text


def test_a_utc_clock_on_a_vpn_network_is_still_refused(installer: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    ui = _UI()
    monkeypatch.delenv("TRW_SKIP_INDEX_PREFLIGHT", raising=False)
    monkeypatch.setattr(installer, "_probe_edge", lambda: {"loc": "US", "warp": "off", "org": "AS9009 M247 Europe"})
    monkeypatch.setattr(installer, "_local_utc_offset", lambda: 0)

    with pytest.raises(SystemExit):
        installer.index_preflight(ui)

    assert "TRW_SKIP_INDEX_PREFLIGHT=1" in " ".join(ui.errors)
