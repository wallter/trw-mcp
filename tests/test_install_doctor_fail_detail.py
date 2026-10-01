"""FB-INSTALL-01 (c): the installer names WHY a doctor row failed, not only its name.

A broken hook family (a kept old lib, hooks calling functions it lacks) reached the user as
``FAIL: hook_family`` followed by a generic ``git init && trw-mcp init-project`` fix, which does not
repair it. Each FAIL row's own message (what broke, which command fixes it) is now printed under it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from tests._install_trw_pip_target_contract_support import _load_installer_module

pytestmark = pytest.mark.integration

_TEMPLATE = Path(__file__).resolve().parents[2] / "trw-mcp" / "scripts" / "install-trw.template.py"


@pytest.fixture(scope="module")
def installer() -> ModuleType:
    return _load_installer_module(_TEMPLATE)


def test_a_doctor_fail_row_prints_its_own_message(
    installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    detail = "session-start.sh: calls 21 function(s) undefined in its lib-trw.sh: trw_hooks_enabled"
    payload = {
        "checks": [{"name": "hook_family", "status": "FAIL", "message": detail}, {"name": "x", "status": "PASS"}]
    }

    def fake_run(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 1, json.dumps(payload), "")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: ["trw-mcp"])
    warned: list[str] = []
    ui = installer.UI.__new__(installer.UI)
    monkeypatch.setattr(installer.UI, "step_warn", lambda _self, text, *a, **k: warned.append(text))
    monkeypatch.setattr(installer.UI, "step_ok", lambda _self, text, *a, **k: None)

    healthy = installer.run_install_doctor(ui, "python3", tmp_path)

    assert healthy is False
    shown = "\n".join(warned)
    assert "hook_family" in shown
    assert detail in shown, "the row's own message (what broke and how to fix it) must reach the user"
