"""Installer statements that were wrong, noisy or unhelpful on a real upgrade (FB-INSTALL-06/10/14/15/16/19).

The operator upgraded TRW on a second machine and the agent there filed what it saw. Every row below is
a line the installer printed that misdescribed the run, repeated itself, or named something the user
cannot open. Each test drives the real function and asserts on what the user would read.

Only the TEMPLATE is loaded; ``dist/install-trw.py`` is rebuilt from it by ``make installer`` and the
template->dist drift gate keeps the two equal.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests._install_trw_main_support import drive_main, make_project
from tests._install_trw_pip_target_contract_support import _load_installer_module

_TEMPLATE = Path(__file__).resolve().parents[1] / "scripts" / "install-trw.template.py"


@pytest.fixture(scope="module")
def installer() -> ModuleType:
    return _load_installer_module(_TEMPLATE)


class _RecordingUI:
    """A UI stand-in that records the verdict lines ``run_install_doctor`` chooses."""

    interactive = False  # run_install_doctor's trw_assess install-check reads it

    def __init__(self) -> None:
        self.ok: list[str] = []
        self.warn: list[str] = []

    def step_ok(self, msg: str) -> None:
        self.ok.append(msg)

    def step_warn(self, msg: str) -> None:
        self.warn.append(msg)


def _doctor_says(installer: ModuleType, monkeypatch: pytest.MonkeyPatch, checks: list[dict[str, str]]) -> _RecordingUI:
    payload = json.dumps({"checks": checks, "overall": "warn"})
    monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: ["trw-mcp"])
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess(args=[], returncode=0, stdout=payload, stderr=""),
    )
    ui = _RecordingUI()
    installer.run_install_doctor(ui, sys.executable, Path("."))
    return ui


# ── FB-INSTALL-06: "health check passed" while doctor ends WARN ──────────


class TestHealthCheckLineMatchesTheDoctorVerdict:
    def test_a_warn_row_is_not_reported_as_passed(self, installer: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        ui = _doctor_says(
            installer,
            monkeypatch,
            [
                {"name": "config", "status": "PASS", "message": "ok"},
                {"name": "mcp_security", "status": "WARN", "message": "1 recent anomaly"},
            ],
        )

        assert not any("passed" in line for line in ui.ok), f"a WARN run was reported as passed: {ui.ok}"
        (line,) = ui.warn
        assert "1 warning" in line
        assert "mcp_security" in line
        assert "trw-mcp doctor" in line

    def test_the_count_and_every_name_are_reported(
        self, installer: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ui = _doctor_says(
            installer,
            monkeypatch,
            [
                {"name": "mcp_security", "status": "WARN", "message": "x"},
                {"name": "pipeline_health", "status": "WARN", "message": "x"},
                {"name": "config", "status": "PASS", "message": "x"},
            ],
        )

        (line,) = ui.warn
        assert "2 warnings" in line
        assert "mcp_security" in line
        assert "pipeline_health" in line

    def test_no_warnings_still_says_passed(self, installer: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-vacuity: the success line survives for a clean run, SKIP rows included."""
        ui = _doctor_says(
            installer,
            monkeypatch,
            [
                {"name": "config", "status": "PASS", "message": "ok"},
                {"name": "distill", "status": "SKIP", "message": "not installed"},
            ],
        )

        assert any("passed" in line for line in ui.ok)
        assert not ui.warn

    def test_a_warn_row_that_carries_its_own_fix_has_that_fix_printed(
        self, installer: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FB-INSTALL-05(a): the summary line named the row but not the command that repairs 2,600 vectors dense recall skips."""
        fix = "2600 stored vector(s) outside the active space; only 40% of stored vectors are in the active space; fix: trw-mcp memory reembed"
        ui = _doctor_says(
            installer,
            monkeypatch,
            [
                {"name": "retrieval", "status": "WARN", "message": fix},
                {"name": "mcp_security", "status": "WARN", "message": "1 recent anomaly"},
            ],
        )

        summary, remedy = ui.warn
        assert "2 warnings" in summary
        assert "retrieval" in remedy and "fix: trw-mcp memory reembed" in remedy
        assert not any("1 recent anomaly" in line for line in ui.warn), (
            "a row with no remedy stays a name in the summary"
        )

    def test_a_warn_only_run_is_still_healthy(self, installer: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        """The return value keeps its contract: True means no FAIL row, WARN included."""
        payload = json.dumps({"checks": [{"name": "x", "status": "WARN", "message": "m"}]})
        monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: ["trw-mcp"])
        monkeypatch.setattr(
            installer.subprocess,
            "run",
            lambda *_a, **_k: subprocess.CompletedProcess(args=[], returncode=0, stdout=payload, stderr=""),
        )

        assert installer.run_install_doctor(_RecordingUI(), sys.executable, Path(".")) is True


# ── FB-INSTALL-10: the same notice once per client ───────────────────────

_NOTICE = "retired_artifact_present: .claude/agent-memory/trw-lead is no longer used by TRW (why); remove it manually: rm -r /p/x"
_CHILD = [sys.executable, "-c", f"print('WARNING: {_NOTICE}'); print('Updated: a-file')"]


class TestARepeatedChildWarningIsShownOnce:
    """``update-project`` runs once per client and prints every project-wide notice again."""

    def test_headless_mode_prints_the_notice_once_across_runs(
        self, installer: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ui = installer.UI(interactive=False)

        for _client in range(4):
            assert installer.run_with_progress(ui, "Updating", _CHILD)

        out = capsys.readouterr().out
        assert out.count("retired_artifact_present") == 1, out
        assert out.count("Updated: a-file") == 4, "ordinary progress lines are not deduplicated"

    def test_interactive_mode_shows_the_notice_once_across_runs(
        self, installer: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ui = installer.UI(interactive=True)

        for _client in range(4):
            assert installer.run_with_progress(ui, "Updating", _CHILD)
            ui.stop_spinner(True, "configured")

        out = capsys.readouterr().out
        assert out.count("retired_artifact_present") == 1, out

    def test_a_different_warning_is_still_shown(
        self, installer: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ui = installer.UI(interactive=False)
        other = [sys.executable, "-c", "print('WARNING: something else entirely')"]

        assert installer.run_with_progress(ui, "Updating", _CHILD)
        assert installer.run_with_progress(ui, "Updating", other)

        out = capsys.readouterr().out
        assert "retired_artifact_present" in out
        assert "something else entirely" in out


# ── FB-INSTALL-14: the upgrade hint must be a command that exists ────────


def _banner(installer: ModuleType, capsys: pytest.CaptureFixture[str]) -> str:
    installer.show_success_banner(installer.UI(interactive=True), "offline", [], is_reinstall=True)
    return capsys.readouterr().out


class TestUpgradeHintNamesARealCommand:
    def test_after_the_curl_bootstrap_it_prints_the_curl_command(
        self,
        installer: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The bootstrap runs a mktemp copy (``install-trw-AbC123``) it deletes on exit."""
        temp_copy = tmp_path / "install-trw-AbC123"
        temp_copy.write_text("# installer\n", encoding="utf-8")
        monkeypatch.setattr(installer.sys, "argv", [str(temp_copy)])

        out = _banner(installer, capsys)

        assert "python3 install-trw.py --upgrade" not in out
        assert "curl -fsSL https://trwframework.com/install.sh | bash" in out

    def test_a_downloaded_install_trw_py_keeps_the_local_command(
        self,
        installer: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        script = tmp_path / "install-trw.py"
        script.write_text("# installer\n", encoding="utf-8")
        monkeypatch.setattr(installer.sys, "argv", [str(script)])

        out = _banner(installer, capsys)

        assert "python3 install-trw.py --upgrade" in out
        assert "install.sh" not in out


# ── FB-INSTALL-15: the distill block ─────────────────────────────────────


def _hint(
    installer: ModuleType, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, missing: list[str]
) -> str:
    monkeypatch.setattr(installer, "_missing_distill_map_extras", lambda *_a, **_k: (missing, True))
    installer._print_distill_repo_intel_hint(
        installer.UI(interactive=False), ["trw-distill 0.9.1"], python=sys.executable
    )
    return capsys.readouterr().out


class TestDistillHintOnlyAdvisesWhatIsMissing:
    def test_no_pip_line_when_every_module_imports(
        self, installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = _hint(installer, capsys, monkeypatch, [])

        assert "pip install" not in out
        assert "tree-sitter" not in out

    def test_the_pip_line_names_only_the_missing_requirements(
        self, installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = _hint(installer, capsys, monkeypatch, ["jedi>=0.19"])

        assert 'pip install "jedi>=0.19"' in out
        assert "tree-sitter-python" not in out

    def test_it_never_cites_a_repo_only_doc_path(
        self, installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``docs/deployment/...`` exists in the TRW repo, not in the project being installed."""
        for missing in ([], ["jedi>=0.19"]):
            out = _hint(installer, capsys, monkeypatch, missing)
            assert "docs/deployment" not in out
            assert "proprietary-distribution" not in out
            assert "trw-distill --help" in out

    def test_the_probe_reports_a_module_that_does_not_import(self, installer: ModuleType) -> None:
        """The real probe, against this interpreter: a module that cannot import is reported, a stdlib one is not."""
        found = installer._probe_missing_modules(sys.executable, ["json", "trw_no_such_module_fb15"], "")

        assert found == ["trw_no_such_module_fb15"]

    def test_an_unrunnable_probe_falls_back_to_advising_everything(self, installer: ModuleType) -> None:
        """Not knowing is not the same as 'all present': say what would help."""
        missing, verified = installer._missing_distill_map_extras("/nonexistent/python-fb15", "")

        assert len(missing) == len(installer._DISTILL_MAP_EXTRAS)
        assert verified is False, "the caller must be able to tell a guess from a measurement"

    def test_an_unverified_advice_says_it_could_not_check(
        self, installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A failed probe is not a finding that the packages are missing; the block must not claim one."""
        everything = [requirement for _module, requirement in installer._DISTILL_MAP_EXTRAS]
        monkeypatch.setattr(installer, "_missing_distill_map_extras", lambda *_a, **_k: (everything, False))

        installer._print_distill_repo_intel_hint(
            installer.UI(interactive=False), ["trw-distill 0.9.1"], python=sys.executable
        )

        out = capsys.readouterr().out
        assert "could not check" in out
        assert "cannot import" not in out
        assert "pip install" in out and '"jedi>=0.19"' in out, "the way to get full map fidelity is still given"

    def test_the_license_errors_do_not_cite_the_repo_doc(self, installer: ModuleType) -> None:
        assert "proprietary-distribution.md" not in installer._PROPRIETARY_PRECONDITION_ERROR


# ── FB-INSTALL-16: an explicit TRW_WITH_PROPRIETARY=1 is the consent ─────


def _auto_confirm_seen(run: Any) -> bool:
    (call,) = run.calls["proprietary"]
    return bool(call[1]["auto_confirm"])


class TestAnExplicitProprietaryRequestIsNotAskedAgain:
    @pytest.fixture(autouse=True)
    def _license(self, installer: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(installer, "_resolve_proprietary_license", lambda **_k: ("lic-not-a-secret", True))

    def test_the_env_flag_skips_the_prompt_in_an_interactive_run(
        self, installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        run = drive_main(
            installer,
            monkeypatch,
            make_project(tmp_path),
            env={"TRW_WITH_PROPRIETARY": "1"},
            interactive=True,
        )

        assert _auto_confirm_seen(run) is True

    def test_the_cli_flag_skips_the_prompt_in_an_interactive_run(
        self, installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        run = drive_main(
            installer,
            monkeypatch,
            make_project(tmp_path),
            extra_argv=("--with-proprietary",),
            interactive=True,
        )

        assert _auto_confirm_seen(run) is True

    def test_a_path_inferred_from_the_marker_still_asks(
        self, installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Nobody consented in this run: a committed marker from an earlier install is the only signal."""
        target = make_project(tmp_path)
        (target / ".trw" / "proprietary-installed.json").write_text('{"trw-loop": "0.1.5"}\n', encoding="utf-8")

        run = drive_main(installer, monkeypatch, target, interactive=True)

        assert _auto_confirm_seen(run) is False

    def test_a_headless_run_is_still_auto_confirmed(
        self, installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        run = drive_main(installer, monkeypatch, make_project(tmp_path), env={"TRW_WITH_PROPRIETARY": "1"})

        assert _auto_confirm_seen(run) is True


# ── FB-INSTALL-19: "Project: a (from prior install)" ─────────────────────


class TestProjectLabelNamesTheKeyItReads:
    def test_the_prior_value_is_labelled_as_installation_id(
        self,
        installer: ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(installer, "_open_tty", lambda: None)
        target = make_project(tmp_path)
        (target / ".trw" / "config.yaml").write_text("installation_id: a\n", encoding="utf-8")
        ui = installer.UI(interactive=True)
        prior = installer._load_prior_config(target)

        installer.phase_configure(
            ui, 1, 1, target, True, "", "", False, prior_config=prior, skip_auth=True, target_platforms=None
        )

        out = capsys.readouterr().out
        project_lines = [line for line in out.splitlines() if "Project:" in line]
        assert project_lines, out
        assert "installation_id" in project_lines[0]
        assert "from prior install" in project_lines[0]
