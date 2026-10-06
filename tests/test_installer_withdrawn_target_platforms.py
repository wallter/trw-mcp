"""Withdrawn client ids in a prior config.yaml: one warning per install, naming gemini's successor.

docs/CLIENT-PROFILES.md ("Withdrawn ids") keeps a stale ``target_platforms`` entry as written. The installer
reads the prior config more than once per install, so the 9.1.0 run printed "Ignoring unknown
target_platforms ... 'gemini', 'aider'" twice, and called two retired clients "unknown".
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"


@pytest.fixture
def installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_withdrawn_ids_probe", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed(target: Path, platforms: list[str]) -> None:
    trw = target / ".trw"
    trw.mkdir(parents=True)
    lines = "".join(f'  - "{p}"\n' for p in platforms)
    (trw / "config.yaml").write_text(f"target_platforms:\n{lines}installation_id: demo\n", encoding="utf-8")


def test_reading_the_prior_config_twice_warns_once_and_keeps_the_valid_clients(
    installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, ["claude-code", "codex", "gemini", "aider"])
    ui = installer.UI(interactive=False)
    shown: list[str] = []
    monkeypatch.setattr(ui, "warn", shown.append)

    first = installer._load_prior_config(tmp_path, ui)
    second = installer._load_prior_config(tmp_path, ui)

    assert first["target_platforms"] == second["target_platforms"] == ["claude-code", "codex"]
    assert len(shown) == 1, shown
    assert shown[0].startswith(
        "Ignoring unknown target_platforms in .trw/config.yaml: 'gemini', 'aider'"
        " (gemini is withdrawn, use antigravity-cli; supported values: claude-code,"
    )
    # The entries stay in config.yaml as written (docs/CLIENT-PROFILES.md, "Withdrawn ids").
    assert '"gemini"' in (tmp_path / ".trw" / "config.yaml").read_text(encoding="utf-8")


def test_a_typo_gets_no_withdrawn_client_hint(installer: ModuleType, tmp_path: Path) -> None:
    _seed(tmp_path, ["claude-code", "vscodium"])
    ui = installer.UI(interactive=False)
    shown: list[str] = []
    ui.warn = shown.append

    installer._load_prior_config(tmp_path, ui)

    assert len(shown) == 1 and "'vscodium'" in shown[0] and "withdrawn" not in shown[0]
