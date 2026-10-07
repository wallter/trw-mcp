"""An update runs ONE plain update-project (coordinator item 15).

Per-client ``update-project --ide X`` runs each reported on the whole project, so their closing Status lines repeated
and could not be summed honestly. An update now records only clients it has not seen (``--ide X``) and then runs a
single plain ``update-project`` that resolves every recorded client itself. A fresh install is unchanged.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"
_RECORDED = ["claude-code", "codex", "copilot", "grok"]


def _installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_update_runs", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Ui:
    interactive = False
    quiet = False

    def __getattr__(self, name: str) -> Any:
        return lambda *a, **k: None


def _project(tmp_path: Path, recorded: list[str] | None) -> Path:
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    if recorded is not None:
        trw = project / ".trw"
        trw.mkdir()
        (trw / "installer-meta.yaml").write_text("framework_version: v1\n", encoding="utf-8")
        (trw / "config.yaml").write_text(
            "installation_id: proj\ntarget_platforms:\n" + "".join(f"  - {c}\n" for c in recorded), encoding="utf-8"
        )
    return project


def _calls(
    monkeypatch: pytest.MonkeyPatch, project: Path, ide: list[str] | None
) -> list[tuple[str, list[str], str, str]]:
    installer = _installer()
    seen: list[tuple[str, list[str], str, str]] = []
    monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: ["trw-mcp"])
    monkeypatch.setattr(
        installer, "_run_project_command", lambda _ui, label, cmd, ok, fail: seen.append((label, cmd, ok, fail))
    )
    installer.phase_project_setup(_Ui(), 3, 4, "python3", project, False, ide=ide)
    return seen


def _verb(call: tuple[str, list[str], str, str]) -> tuple[str, str | None]:
    cmd = call[1]
    return cmd[1], (cmd[cmd.index("--ide") + 1] if "--ide" in cmd else None)


def test_an_update_of_four_recorded_clients_runs_one_plain_update_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = _project(tmp_path, _RECORDED)

    calls = _calls(monkeypatch, project, None)

    assert [_verb(c) for c in calls] == [("update-project", None)]
    assert calls[0][1] == ["trw-mcp", "update-project", str(project)]
    label, _cmd, ok, fail = calls[0]
    for name in ("Claude Code", "Codex", "Copilot", "Grok"):
        assert name in label and name in ok and name in fail  # one line naming every client


def test_an_update_adding_a_new_client_records_it_then_runs_one_plain_update(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = _project(tmp_path, _RECORDED)

    calls = _calls(monkeypatch, project, [*_RECORDED, "opencode"])

    assert [_verb(c) for c in calls] == [("update-project", "opencode"), ("update-project", None)]
    assert "OpenCode" in calls[1][0]  # the closing step names the new client too


def test_a_deselected_recorded_client_is_not_run_per_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project = _project(tmp_path, _RECORDED)

    calls = _calls(monkeypatch, project, ["claude-code"])

    assert [_verb(c) for c in calls] == [("update-project", None)]  # the plain run still maintains the recorded four


def test_a_fresh_install_is_unchanged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project = _project(tmp_path, None)

    calls = _calls(monkeypatch, project, ["claude-code", "codex"])

    assert [_verb(c) for c in calls] == [("init-project", "claude-code"), ("update-project", "codex")]
    assert calls[0][2] == "Claude Code configured"


def test_a_single_client_fresh_install_still_says_initialized(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = _calls(monkeypatch, _project(tmp_path, None), ["claude-code"])
    assert [(_verb(c), c[2]) for c in calls] == [(("init-project", "claude-code"), "Project initialized")]


def test_configure_keeps_every_recorded_client_when_only_one_is_selected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Review P1-3: configure replaced target_platforms with this run's clients, so later plain updates lost three."""
    installer = _installer()
    project = _project(tmp_path, _RECORDED)

    installer.phase_configure(
        installer.UI(interactive=False), 4, 5, project, False, "", "", None, {},
        skip_auth=True, target_platforms=["claude-code"],
    )  # fmt: skip

    recorded = installer._load_prior_config(project)["target_platforms"]
    assert recorded == _RECORDED  # all four, in their recorded order
    # and the next plain update covers all four
    calls = _calls(monkeypatch, project, None)
    assert [_verb(c) for c in calls] == [("update-project", None)]
    for name in ("Claude Code", "Codex", "Copilot", "Grok"):
        assert name in calls[0][0]


def test_configure_adds_a_new_client_to_the_recorded_ones(tmp_path: Path) -> None:
    installer = _installer()
    project = _project(tmp_path, _RECORDED)

    installer.phase_configure(
        installer.UI(interactive=False), 4, 5, project, False, "", "", None, {},
        skip_auth=True, target_platforms=["opencode", "claude-code"],
    )  # fmt: skip

    assert installer._load_prior_config(project)["target_platforms"] == [*_RECORDED, "opencode"]
