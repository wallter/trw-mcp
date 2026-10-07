"""A renamed package left behind in the active interpreter is reported with its uninstall command (user report
sub_1-sANJtIT9H-nx7q item 8: ``trw-harness`` 0.1.2, renamed ``trw-metaharness``, was never mentioned)."""

from __future__ import annotations

import importlib.util
import shlex
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"


def _installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_leftovers", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Ui:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def step_warn(self, text: str) -> None:
        self.warnings.append(text)


def _report(monkeypatch: pytest.MonkeyPatch, python: str, versions: dict[str, str]) -> list[str]:
    installer = _installer()

    def fake(cmd: list[str], target_dir: str = "", timeout: int = 60) -> tuple[int, str]:
        name = next((n for n in versions if repr(n) in cmd[-1]), "")
        return (0, versions[name] + "\n") if name else (1, "")

    monkeypatch.setattr(installer, "_run_python_output", fake)
    ui = _Ui()
    installer.report_leftover_packages(ui, python)
    return ui.warnings


def test_a_leftover_trw_harness_is_reported_with_the_exact_uninstall_command(monkeypatch: pytest.MonkeyPatch) -> None:
    (warning,) = _report(monkeypatch, "/opt/my env/bin/python", {"trw-harness": "0.1.2"})

    assert "trw-harness 0.1.2" in warning and "trw-metaharness" in warning
    command = warning.split("remove it with: ", 1)[1]
    assert shlex.split(command) == ["/opt/my env/bin/python", "-m", "pip", "uninstall", "trw-harness"]


def test_nothing_is_said_when_the_package_is_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _report(monkeypatch, sys.executable, {}) == []


def test_the_leftover_check_never_raises_or_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    installer = _installer()

    def boom(*_a: Any, **_k: Any) -> tuple[int, str]:
        raise OSError("no interpreter")

    monkeypatch.setattr(installer, "_run_python_output", boom)
    ui = _Ui()
    installer.report_leftover_packages(ui, "/nowhere/python")  # advisory: a failed probe says nothing
    assert ui.warnings == []


def test_main_runs_the_check_after_the_packages_are_installed() -> None:
    text = _TEMPLATE.read_text(encoding="utf-8")
    assert text.index("report_leftover_packages(ui, python, args.pip_target)") > text.index(
        "persist_answers_as_given(ui, python, target_dir, args.pip_target)"
    )


def _report_in(monkeypatch: pytest.MonkeyPatch, python: str, target: Path) -> list[str]:
    installer = _installer()
    monkeypatch.setattr(installer, "_run_python_output", lambda *_a, **_k: (0, "0.1.2\n"))
    ui = _Ui()
    installer.report_leftover_packages(ui, python, str(target))
    return ui.warnings


def test_a_target_dir_install_names_the_package_directories_to_remove(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Under --pip-target the copy lives in that directory: `pip uninstall` would look elsewhere and miss it."""
    target = tmp_path / "site target"
    for name in ("trw_harness", "trw_harness-0.1.2.dist-info", "trw_metaharness", "unrelated"):
        (target / name).mkdir(parents=True)

    (warning,) = _report_in(monkeypatch, "/usr/bin/python3", target)

    command = warning.split("remove it with: ", 1)[1]
    assert "pip uninstall" not in command
    assert shlex.split(command) == [
        "rm",
        "-rf",
        str(target / "trw_harness"),
        str(target / "trw_harness-0.1.2.dist-info"),
    ]  # the renamed package's own directory and metadata, nothing else
    assert str(target) in warning


def test_a_target_dir_that_does_not_hold_it_falls_back_to_the_interpreters_pip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "empty-target").mkdir()

    (warning,) = _report_in(monkeypatch, "/usr/bin/python3", tmp_path / "empty-target")

    assert shlex.split(warning.split("remove it with: ", 1)[1]) == [
        "/usr/bin/python3",
        "-m",
        "pip",
        "uninstall",
        "trw-harness",
    ]


def test_a_same_prefix_unrelated_package_is_never_suggested_for_removal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "target"
    for name in (
        "trw_harness",
        "trw_harness-0.1.2.dist-info",
        "trw_harness_plugin",
        "trw_harness_plugin-1.0.dist-info",
        "trw_harnessed",
    ):
        (target / name).mkdir(parents=True)

    (warning,) = _report_in(monkeypatch, "/usr/bin/python3", target)

    assert shlex.split(warning.split("remove it with: ", 1)[1]) == [
        "rm",
        "-rf",
        str(target / "trw_harness"),
        str(target / "trw_harness-0.1.2.dist-info"),
    ]
    assert "plugin" not in warning and "harnessed" not in warning


def test_a_target_holding_only_an_unrelated_package_falls_back_to_pip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "target"
    (target / "trw_harness_plugin").mkdir(parents=True)  # the harness itself is in the interpreter, not here

    (warning,) = _report_in(monkeypatch, "/usr/bin/python3", target)

    assert "rm -rf" not in warning
    assert shlex.split(warning.split("remove it with: ", 1)[1])[-3:] == ["pip", "uninstall", "trw-harness"]


def test_only_the_entries_the_record_lists_are_named(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "target"
    dist = target / "trw_harness-0.1.2.dist-info"
    dist.mkdir(parents=True)
    (dist / "RECORD").write_text(
        "trw_harness-0.1.2.dist-info/METADATA,,\n", encoding="utf-8"
    )  # does not list the package
    (target / "trw_harness").mkdir()

    (warning,) = _report_in(monkeypatch, "/usr/bin/python3", target)

    assert shlex.split(warning.split("remove it with: ", 1)[1]) == ["rm", "-rf", str(dist)]

    (dist / "RECORD").write_text("trw_harness/__init__.py,,\ntrw_harness-0.1.2.dist-info/RECORD,,\n", encoding="utf-8")
    (warning,) = _report_in(monkeypatch, "/usr/bin/python3", target)
    assert shlex.split(warning.split("remove it with: ", 1)[1]) == ["rm", "-rf", str(target / "trw_harness"), str(dist)]


def test_a_global_only_install_uses_the_interpreters_pip(monkeypatch: pytest.MonkeyPatch) -> None:
    installer = _installer()
    monkeypatch.setattr(installer, "_run_python_output", lambda *_a, **_k: (0, "0.1.2\n"))
    ui = _Ui()
    installer.report_leftover_packages(ui, "/usr/bin/python3")  # no --pip-target

    (warning,) = ui.warnings
    assert shlex.split(warning.split("remove it with: ", 1)[1]) == [
        "/usr/bin/python3",
        "-m",
        "pip",
        "uninstall",
        "trw-harness",
    ]
