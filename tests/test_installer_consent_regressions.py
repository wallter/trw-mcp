"""Remember decisions, never infer consent or refusal from unattended/failed prompts."""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

from tests._install_trw_main_support import drive_main, make_project

pytestmark = pytest.mark.unit


@pytest.fixture
def installer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    template = Path(__file__).resolve().parents[1] / "scripts/install-trw.template.py"
    spec = importlib.util.spec_from_file_location("installer_consent", template)
    assert spec is not None and spec.loader is not None
    inst = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inst)
    monkeypatch.delenv("TRW_WITH_PROPRIETARY", raising=False)
    inst.set_answer_store(inst.AnswerStore(tmp_path / "config.yaml", tmp_path / "machine.yaml"))
    return inst


@pytest.mark.parametrize("explicit", [False, True])
def test_remembered_upgrade_decline_beats_headless_default(
    installer: ModuleType, tmp_path: Path, explicit: bool
) -> None:
    marker = tmp_path / installer.PROPRIETARY_MARKER_RELPATH
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('{"trw-distill": "1.0"}')
    installer._ANSWERS.remember("proprietary_upgrade", False)
    assert installer._resolve_proprietary_from_marker(tmp_path, explicit, False, Mock()) is explicit


@pytest.mark.parametrize("explicit", [False, True])
def test_main_only_auto_confirms_an_explicit_proprietary_request(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, explicit: bool
) -> None:
    project = make_project(tmp_path)
    (project / installer.PROPRIETARY_MARKER_RELPATH).write_text('{"trw-distill": "1.0"}')
    run = drive_main(
        installer,
        monkeypatch,
        project,
        env={"TRW_LICENSE_KEY": "test-license"},
        extra_argv=("--with-proprietary",) if explicit else (),
    )
    assert len(run.calls["proprietary"]) == 1
    assert run.calls["proprietary"][0][1]["auto_confirm"] is explicit


def test_bound_install_decline_prevents_fetch(installer: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = "https://example.invalid"
    consent = installer.bound_key(
        "proprietary_install",
        installer._backend_origin(backend),
        *(f"{pkg}==latest" for pkg in installer.PROPRIETARY_PACKAGES_TUPLE),
    )
    installer._ANSWERS.remember(consent, False)
    monkeypatch.setattr(installer, "_open_tty", lambda: None)
    fetch = Mock(side_effect=AssertionError("declined install fetched wheels"))
    monkeypatch.setattr(installer, "_post_proprietary_entitlement", fetch)
    assert installer.phase_install_proprietary(Mock(), 1, 1, "python", "license", {}, backend) == []
    fetch.assert_not_called()


@pytest.mark.parametrize("scenario", ["invalid", "eof", "io_error", "device_error", "device_empty", "skip"])
def test_platform_only_remembers_an_explicit_skip(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scenario: str
) -> None:
    monkeypatch.setattr(installer, "_prompt_project_name", lambda *_a: "project")
    monkeypatch.setattr(installer, "prompt_yes_no", lambda *_a, **_kw: scenario.startswith("device"))
    auth = Mock(side_effect=RuntimeError("offline")) if scenario == "device_error" else Mock(return_value=None)
    monkeypatch.setattr(installer, "_device_auth_login", auth)
    monkeypatch.setattr(installer, "_resolve_interactive_telemetry", lambda *_a, **_kw: False)
    monkeypatch.setattr(installer, "_open_tty_available", lambda: True)
    raw = {"invalid": "mistyped\n", "eof": ""}.get(scenario, "\n")
    tty = Mock(side_effect=OSError("unreadable terminal")) if scenario == "io_error" else lambda: io.StringIO(raw)
    monkeypatch.setattr(installer, "_open_tty", tty)
    project = make_project(tmp_path)
    installer.phase_configure(Mock(), 1, 1, project, True, "", "", None, {}, skip_auth=False)
    assert installer.recorded_answer("platform_connect") is (False if scenario == "skip" else None)
