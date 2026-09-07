"""An ``update-project`` warning has to survive the installer's spinner.

``_record_sync_refusals`` puts a PRD-FIX-123 policy refusal into
``result["warnings"]`` — the operator's only signal that their CLAUDE.md was
deliberately left stale. Two separate defects then threw it away:

* the CLI printed refusals as ``- <text>`` bullets, and only when the update had
  no errors and was not quiet;
* ``install-trw.py``'s ``run_with_progress`` matched child lines against
  ``r"  *(Updated|…|WARNING|Error):? "`` — a pattern requiring leading
  whitespace, applied to a line it had already ``strip()``ped, so nothing
  matched and every child line was read and discarded.

The two surfaces now meet on one contract: ``WARNING: <text>``. This file tests
the producer (``server/_subcommands.py``), the consumer
(``run_with_progress``), and that they still agree.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

from tests._install_trw_pip_target_contract_support import _load_installer_module

_TEMPLATE = Path(__file__).resolve().parents[1] / "scripts" / "install-trw.template.py"

_REFUSAL = (
    "CLAUDE.md NOT updated — refused (total_shrink): the write would have shrunk the file more than the guard allows"
)


@pytest.fixture(scope="module")
def installer() -> ModuleType:
    return _load_installer_module(_TEMPLATE)


def _args(**overrides: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "target_dir": ".",
        "pip_install": False,
        "dry_run": False,
        "ide": "claude-code",
        "log_json": False,
        "debug": False,
        "verbose": 0,
        "quiet": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _result(**overrides: object) -> dict[str, list[str]]:
    base: dict[str, list[str]] = {
        "updated": ["CLAUDE.md"],
        "created": [],
        "preserved": [],
        "errors": [],
        "warnings": [_REFUSAL],
        "cleaned": [],
    }
    base.update(overrides)  # type: ignore[arg-type]
    return base


def _run_update(args: argparse.Namespace, result: dict[str, list[str]]) -> int:
    from trw_mcp.server._subcommands import _run_update_project

    with patch("trw_mcp.bootstrap.update_project", return_value=result), pytest.raises(SystemExit) as exc:
        _run_update_project(args)
    return int(exc.value.code or 0)


# ── Producer: the CLI ────────────────────────────────────────────────────


class TestTheCliAlwaysSurfacesWarnings:
    def test_a_refusal_is_printed_with_the_stable_prefix(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = _run_update(_args(), _result())

        out = capsys.readouterr().out
        assert code == 0
        assert f"WARNING: {_REFUSAL}" in out

    def test_a_refusal_is_printed_even_when_the_update_also_errored(self, capsys: pytest.CaptureFixture[str]) -> None:
        """THE defect: warnings hung off the no-errors summary branch.

        The runs most likely to carry a warning were exactly the runs whose
        summary never rendered.
        """
        code = _run_update(_args(), _result(errors=["something else broke"]))

        out = capsys.readouterr().out
        assert code == 1
        assert f"WARNING: {_REFUSAL}" in out

    def test_quiet_still_suppresses_human_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The one documented exception: ``--quiet`` means quiet."""
        _run_update(_args(quiet=True), _result())

        assert capsys.readouterr().out == ""

    def test_verbose_mode_logs_the_warning_instead_of_printing_it(self, capsys: pytest.CaptureFixture[str]) -> None:
        """``-v`` routes through structlog; it must not double-print."""
        import structlog

        with structlog.testing.capture_logs() as logs:
            _run_update(_args(verbose=1), _result())

        assert "WARNING:" not in capsys.readouterr().out
        assert any(entry.get("event") == "update_project_warning" for entry in logs)


# ── Consumer: the installer's progress reader ────────────────────────────


def _child(*lines: str) -> list[str]:
    """A real child process emitting *lines* on stdout."""
    program = "\n".join(f"print({line!r}, flush=True)" for line in lines)
    return [sys.executable, "-c", program]


class TestRunWithProgressReSurfacesWarnings:
    def test_an_interactive_run_shows_the_warning_after_the_spinner_stops(
        self, installer: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """THE defect: in interactive mode the refusal never reached the user."""
        ui = installer.UI(interactive=True)
        cmd = _child("Phase: Syncing CLAUDE.md", "Updated: .claude/hooks/session-start.sh", f"WARNING: {_REFUSAL}")

        ok = installer.run_with_progress(ui, "Updating project...", cmd)
        ui.stop_spinner(ok, "Project updated")

        out = capsys.readouterr().out
        assert ok is True
        assert _REFUSAL in out, "the operator must still be told their CLAUDE.md was left stale"
        assert out.index("Project updated") < out.index(_REFUSAL), (
            "a warning printed under a live spinner is repainted away — it must come after stop_spinner"
        )

    def test_a_headless_run_still_streams_the_warning(
        self, installer: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Non-interactive mode echoes every child line; nothing is duplicated."""
        ui = installer.UI(interactive=False)
        cmd = _child(f"WARNING: {_REFUSAL}")

        installer.run_with_progress(ui, "Updating project...", cmd)
        ui.stop_spinner(True, "Project updated")

        out = capsys.readouterr().out
        assert out.count(_REFUSAL) == 1

    def test_ordinary_progress_lines_are_not_promoted_to_warnings(
        self, installer: ModuleType, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Negative control: only warnings survive the spinner."""
        ui = installer.UI(interactive=True)
        cmd = _child("Updated: .claude/settings.json", "Preserved: .mcp.json")

        installer.run_with_progress(ui, "Updating project...", cmd)
        ui.stop_spinner(True, "Project updated")

        out = capsys.readouterr().out
        assert ".mcp.json" not in out.split("Project updated")[-1]


# ── The two surfaces agree ───────────────────────────────────────────────


def test_the_cli_warning_lines_match_the_installer_pattern(
    installer: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """The contract itself: what the CLI prints is what the installer keys on.

    Asserting each side separately is what allowed them to drift — the CLI's
    ``- <text>`` bullet was perfectly readable and matched nothing.
    """
    _run_update(_args(), _result())
    printed = [line.strip() for line in capsys.readouterr().out.splitlines()]

    matched = [line for line in printed if installer._WARNING_LINE_RE.match(line)]
    assert len(matched) == 1
    assert installer._WARNING_LINE_RE.sub("", matched[0], count=1).strip() == _REFUSAL
