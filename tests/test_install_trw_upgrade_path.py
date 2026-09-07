"""The install-trw.py upgrade path: does ``--upgrade`` actually upgrade anything?

Two defects confirmed by the 2026-09-07 upgrade-path audit, each of which made a
re-run of the installer report success while leaving the project behind:

C01 ``_deployed_framework_is_stale`` compared ``framework_protocol_version`` with
    ``installed_asset_version`` — both are the framework PROTOCOL version, which
    is deliberately stable across package releases (unchanged since 2026-07-27,
    identical on 1.0.5 and 2.0.1). The predicate could not fire, so every
    ``--upgrade`` printed "framework already current" and never ran
    ``update-project``.
C08 ``phase_project_setup`` passed only THIS run's clients to ``update_config``,
    which rewrites the ``target_platforms`` block wholesale — undoing the
    append-only guarantee (PRD-FIX-076) that the bootstrap recorder maintains.

The third (C04, the write-only ``.trw/proprietary-installed.json`` marker) lives
with the rest of the proprietary-path coverage in the repo-root
``tests/test_installer_proprietary.py``.

Only the TEMPLATE is loaded. ``dist/install-trw.py`` is a generated, gitignored
build artifact; template↔dist parity is its own gate
(``tests/test_installer_drift_gate.py``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from tests._install_trw_main_support import drive_main, make_project
from tests._install_trw_pip_target_contract_support import _load_installer_module

_TEMPLATE = Path(__file__).resolve().parents[1] / "scripts" / "install-trw.template.py"

#: A version-status payload for a project whose deployed assets match the
#: freshly-installed package. Shaped like the real one
#: (``server/_subcommands_release.py::collect_version_status``).
_CURRENT_STATUS: dict[str, object] = {
    "versions": {
        "packages": {"trw-mcp": "2.0.1"},
        "framework_protocol_version": "v26.2_TRW",
        "installed_asset_version": "v26.2_TRW",
        "installed_asset_trw_mcp_version": "2.0.1",
        "installed_asset_present": True,
    },
    "mismatches": [],
}


def _status(**version_overrides: object) -> dict[str, object]:
    versions = dict(_CURRENT_STATUS["versions"])  # type: ignore[arg-type]
    versions.update(version_overrides)
    return {"versions": versions, "mismatches": []}


@pytest.fixture(scope="module")
def installer() -> ModuleType:
    return _load_installer_module(_TEMPLATE)


def _prior_install(tmp_path: Path, targets: list[str]) -> Path:
    target = tmp_path / "project"
    (target / ".git").mkdir(parents=True)
    trw = target / ".trw"
    trw.mkdir()
    (trw / "installer-meta.yaml").write_text("framework_version: v26.2_TRW\n", encoding="utf-8")
    (trw / "config.yaml").write_text(
        "installation_id: proj\ntarget_platforms:\n" + "".join(f"  - {t}\n" for t in targets),
        encoding="utf-8",
    )
    return target


def _drive_upgrade(
    installer: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    target: Path,
    status: dict[str, object],
) -> tuple[list[list[str]], MagicMock]:
    """Run the real ``phase_project_setup(upgrade_only=True)``. Returns (cmds, ui)."""
    run_calls: list[list[str]] = []
    ui = MagicMock()

    monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: ["trw-mcp"])
    monkeypatch.setattr(installer, "_version_status_payload", lambda *_a, **_k: status)
    monkeypatch.setattr(installer, "run_with_progress", lambda _ui, _label, cmd: run_calls.append(cmd) or True)

    installer.phase_project_setup(ui, 3, 4, sys.executable, target, True)
    return run_calls, ui


# ── C01: the staleness predicate ─────────────────────────────────────────


class TestDeployedFrameworkStaleness:
    """The predicate that decides whether ``--upgrade`` refreshes the project."""

    def test_an_older_asset_package_version_is_stale(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE defect: identical protocol versions, older stamped trw-mcp version.

        This is what every real upgrade looks like — the protocol version does
        not move per release, so comparing it alone answered "current" for a
        project whose deployed assets were a whole package release behind.
        """
        monkeypatch.setattr(
            installer,
            "_version_status_payload",
            lambda *_a, **_k: _status(installed_asset_trw_mcp_version="1.0.5"),
        )

        assert installer._deployed_framework_is_stale(tmp_path, sys.executable) is True

    def test_a_current_asset_is_not_stale(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Negative case. Re-running update-project on every upgrade is not the fix."""
        monkeypatch.setattr(installer, "_version_status_payload", lambda *_a, **_k: _CURRENT_STATUS)

        assert installer._deployed_framework_is_stale(tmp_path, sys.executable) is False

    def test_a_differing_protocol_version_is_still_stale(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The original comparison is kept, not replaced — a v25 project must refresh."""
        monkeypatch.setattr(
            installer,
            "_version_status_payload",
            lambda *_a, **_k: _status(installed_asset_version="v25_TRW"),
        )

        assert installer._deployed_framework_is_stale(tmp_path, sys.executable) is True

    def test_an_absent_asset_manifest_is_stale(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            installer,
            "_version_status_payload",
            lambda *_a, **_k: _status(installed_asset_present=False),
        )

        assert installer._deployed_framework_is_stale(tmp_path, sys.executable) is True

    def test_any_reported_mismatch_is_stale(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``version-status`` already computes the comparison; trust its verdict."""
        payload = dict(_CURRENT_STATUS)
        payload["mismatches"] = ["trw_mcp_package_vs_live_server"]
        monkeypatch.setattr(installer, "_version_status_payload", lambda *_a, **_k: payload)

        assert installer._deployed_framework_is_stale(tmp_path, sys.executable) is True

    def test_an_unusable_probe_is_not_stale(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail-open: a probe that could not answer must not block the upgrade."""
        monkeypatch.setattr(installer, "_version_status_payload", lambda *_a, **_k: {})

        assert installer._deployed_framework_is_stale(tmp_path, sys.executable) is False

    def test_the_probe_seam_returns_empty_when_the_cli_cannot_run(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-vacuity for the fail-open case above: the real seam, real failure."""
        monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: ["definitely-not-a-real-binary-xyz"])

        assert installer._version_status_payload(tmp_path, sys.executable) == {}


class TestUpgradeRunsUpdateProject:
    """The predicate driven through the branch it actually gates."""

    def test_a_stale_project_is_refreshed_for_every_recorded_client(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = _prior_install(tmp_path, ["claude-code", "codex"])

        run_calls, _ui = _drive_upgrade(
            installer, monkeypatch, target, _status(installed_asset_trw_mcp_version="1.0.5")
        )

        assert run_calls == [
            ["trw-mcp", "update-project", str(target), "--ide", "claude-code"],
            ["trw-mcp", "update-project", str(target), "--ide", "codex"],
        ], "an upgrade must refresh the deployed assets for each recorded client"

    def test_a_current_project_short_circuits(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = _prior_install(tmp_path, ["claude-code"])

        run_calls, ui = _drive_upgrade(installer, monkeypatch, target, _CURRENT_STATUS)

        assert run_calls == [], "nothing to refresh ⇒ no update-project subprocess"
        assert any("already current" in str(call.args[0]) for call in ui.step_ok.call_args_list), (
            "the operator is told why nothing ran"
        )

    def test_doctor_runs_on_the_upgrade_path(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An upgrade that left the framework broken must not exit on a green banner.

        The doctor call used to be guarded by ``if not args.upgrade:``, so the one
        path that now rewrites deployed assets was the one path never health-checked.

        Driven through the real ``main()`` rather than a source grep: the grep
        this replaced matched on exact indentation and a literal newline, so it
        would have gone green again the moment the guard was re-introduced in any
        other shape.
        """
        target = make_project(tmp_path)

        run = drive_main(installer, monkeypatch, target, extra_argv=("--upgrade",))

        assert len(run.calls["doctor"]) == 1, "the upgrade path must be health-checked"
        assert run.calls["project_setup"], "and it is the same run that refreshed the project"

    def test_doctor_also_runs_on_the_ordinary_install_path(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Control: the assertion above is about the upgrade branch, not about main()."""
        target = make_project(tmp_path)

        run = drive_main(installer, monkeypatch, target)

        assert len(run.calls["doctor"]) == 1

    def test_a_stale_project_with_no_recorded_client_is_skipped_not_guessed(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An upgrade must not invent a client the project never configured.

        ``--upgrade`` on a project with ``installer-meta.yaml`` but an empty
        ``target_platforms`` used to default to ``["claude-code"]``, scaffolding
        ``.claude/``, ``CLAUDE.md`` and the Claude hooks into a project whose user
        may run codex or opencode exclusively.
        """
        target = _prior_install(tmp_path, [])

        run_calls, ui = _drive_upgrade(installer, monkeypatch, target, _status(installed_asset_trw_mcp_version="1.0.5"))

        assert run_calls == [], "no client recorded ⇒ nothing is scaffolded"
        warnings = [str(call.args[0]) for call in ui.step_warn.call_args_list]
        assert any("no client is recorded" in w for w in warnings), warnings


# ── C08: target_platforms is a union, never a replacement ────────────────


class TestTargetPlatformsUnion:
    """``update_config`` rewrites the block wholesale; the recorder is append-only."""

    def _read_targets(self, target: Path) -> list[str]:
        text = (target / ".trw" / "config.yaml").read_text(encoding="utf-8")
        out: list[str] = []
        collecting = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("target_platforms:"):
                collecting = True
                continue
            if collecting:
                if stripped.startswith("- "):
                    out.append(stripped[2:].strip().strip("\"'"))
                    continue
                if stripped and not stripped.startswith("#"):
                    break
        return out

    def test_a_single_client_run_does_not_narrow_the_recorded_list(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE defect: ``--ide codex`` dropped claude-code out of a two-client project."""
        target = _prior_install(tmp_path, ["claude-code", "codex"])

        monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: ["trw-mcp"])
        monkeypatch.setattr(installer, "run_with_progress", lambda *_a, **_k: True)
        monkeypatch.setattr(installer, "_provision_user_scope", lambda _c: False)
        monkeypatch.setattr(installer, "_detect_installed_clis", list)
        monkeypatch.setattr(installer, "_detect_project_ides", lambda _p: [])

        resolved = installer.phase_project_setup(
            MagicMock(), 3, 4, sys.executable, target, False, interactive=False, ide=["codex"]
        )

        assert resolved == ["codex"], "this run still configures only the named client"
        assert self._read_targets(target) == ["claude-code", "codex"], (
            "the recorded client list must not lose a client the user already had"
        )

    def test_a_new_client_is_appended_in_stable_order(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = _prior_install(tmp_path, ["claude-code"])

        monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: ["trw-mcp"])
        monkeypatch.setattr(installer, "run_with_progress", lambda *_a, **_k: True)
        monkeypatch.setattr(installer, "_provision_user_scope", lambda _c: False)
        monkeypatch.setattr(installer, "_detect_installed_clis", list)
        monkeypatch.setattr(installer, "_detect_project_ides", lambda _p: [])

        installer.phase_project_setup(
            MagicMock(), 3, 4, sys.executable, target, False, interactive=False, ide=["copilot"]
        )

        assert self._read_targets(target) == ["claude-code", "copilot"]


# ── The probe seam: an exit code is not a verdict ────────────────────────


_FAILING_PROBE = (
    "import sys; sys.stdout.write('{\"versions\": {}}'); sys.stderr.write('boom: no such project\\n'); sys.exit(3)"
)
_OK_PROBE = 'import sys; sys.stdout.write(\'{"versions": {"installed_asset_present": false}}\')'


class TestTheProbeHonoursTheExitCode:
    """``_version_status_payload`` used to parse stdout whatever the CLI did."""

    def test_a_non_zero_exit_is_unknown_not_an_answer(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A real child process, a real non-zero exit, real JSON on stdout.

        The payload is discarded (``{}`` ⇒ the caller's documented fail-open
        "not stale"), and the operator is told the probe could not answer —
        with the return code and the stderr tail, so a broken install is not
        silently indistinguishable from a current one.
        """
        ui = MagicMock()
        monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: [sys.executable, "-c", _FAILING_PROBE])

        payload = installer._version_status_payload(tmp_path, sys.executable, ui=ui)

        assert payload == {}
        assert installer._deployed_framework_is_stale(tmp_path, sys.executable, ui=ui) is False
        warned = " ".join(str(call.args[0]) for call in ui.step_warn.call_args_list)
        assert "3" in warned and "boom" in warned, warned

    def test_a_zero_exit_is_still_parsed(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-vacuity: the same seam, same shape, exit 0 ⇒ the payload is used."""
        ui = MagicMock()
        monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: [sys.executable, "-c", _OK_PROBE])

        payload = installer._version_status_payload(tmp_path, sys.executable, ui=ui)

        assert payload == {"versions": {"installed_asset_present": False}}
        assert ui.step_warn.call_count == 0


# ── Fixture-vs-system: the same predicate against the REAL CLI ───────────

_REPO_TRW_MCP = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "trw-mcp"


@pytest.mark.skipif(not _REPO_TRW_MCP.is_file(), reason="repo .venv trw-mcp CLI not built")
class TestAgainstTheRealVersionStatusCli:
    """Everything above stubs ``version-status``. This test does not.

    The hand-written ``_CURRENT_STATUS`` fixture asserts a payload shape — that
    a current project reports ``mismatches: []`` and stamps
    ``installed_asset_trw_mcp_version``. If the real CLI ever emitted something
    else (an always-present ``live_process_currentness_*`` entry, say), every
    stubbed test above would keep passing while the installer decided the
    opposite thing in production. This drives the real binary against a real
    project and pins BOTH verdicts.
    """

    def _project(self, tmp_path: Path, framework: str, mcp_version: str) -> Path:
        project = tmp_path / "user-project"
        frameworks = project / ".trw" / "frameworks"
        frameworks.mkdir(parents=True, exist_ok=True)
        (frameworks / "VERSION.yaml").write_text(
            f"framework_version: {framework}\ntrw_mcp_version: {mcp_version}\n", encoding="utf-8"
        )
        return project

    def test_the_predicate_agrees_with_the_installed_cli(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: [str(_REPO_TRW_MCP)])

        # 1. Ask the CLI what "current" means for THIS install — never hardcode it.
        probe = self._project(tmp_path, "probe", "0.0.0")
        payload = installer._version_status_payload(probe, sys.executable)
        assert payload, "the real CLI must produce a parseable payload"
        versions = payload["versions"]
        assert isinstance(versions, dict)
        current_framework = str(versions["framework_protocol_version"])
        packages = versions["packages"]
        assert isinstance(packages, dict)
        current_mcp = str(packages["trw-mcp"])

        # 2. A project stamped with exactly those versions is NOT stale.
        current = self._project(tmp_path, current_framework, current_mcp)
        assert installer._deployed_framework_is_stale(current, sys.executable) is False, (
            f"real payload for a current project: {installer._version_status_payload(current, sys.executable)}"
        )

        # 3. The same project one package release behind IS stale.
        stale = self._project(tmp_path, current_framework, "0.0.1")
        assert installer._deployed_framework_is_stale(stale, sys.executable) is True

    def test_an_absent_manifest_is_stale_against_the_real_cli(
        self, installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: [str(_REPO_TRW_MCP)])
        bare = tmp_path / "bare"
        bare.mkdir()

        assert installer._deployed_framework_is_stale(bare, sys.executable) is True
