"""The installer shows update-project's whole refusal, not its first line (feedback sub_2K6zdxKjDwf6VBkr).

The refusal is a block: a sentence ending in a colon, one ``<path> -> <target>`` line per symlink, the clients
affected and a ``Fix:`` line. The progress reader strips and the console filter kept only lines containing "error",
so the user saw a sentence ending in a colon and nothing after it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"
_BLOCK = [
    "Error: Failed to snapshot update targets: transaction directory contains a symlinked directory (or a managed "
    "surface is a symlink); TRW never writes through symlinks and updated nothing:",
    ".grok/skills/trw-deliver -> /real/elsewhere/trw-deliver",
    ".claude/skills/trw-deliver -> /real/elsewhere/trw-deliver",
    "Clients affected: Claude Code (.claude), Grok Build CLI (.grok)",
    "Fix: replace each symlink with a real directory (copy its contents in), then re-run.",
]


def _installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_refusal_relay", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Ui:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.fails: list[str] = []

    def error(self, text: str) -> None:
        self.errors.append(text)

    def step_fail(self, text: str) -> None:
        self.fails.append(text)

    def stop_spinner(self, *_a: Any) -> None:
        return None


def _run(monkeypatch: pytest.MonkeyPatch, output: list[str], cmd: list[str] | None = None) -> _Ui:
    installer = _installer()

    def fake(_ui: Any, _label: str, _cmd: list[str], output: list[str] | None = None, **_k: Any) -> bool:
        assert output is not None
        output.extend(globals()["_OUT"])
        return False

    globals()["_OUT"] = output
    monkeypatch.setattr(installer, "run_with_progress", fake)
    ui = _Ui()
    with pytest.raises(SystemExit):
        installer._run_project_command(
            ui, "Updating...", cmd or ["x"], "ok", "Project update-project failed for Claude Code"
        )
    return ui


def test_every_line_of_the_refusal_is_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = _run(monkeypatch, ["==> Phase", "Warning: noise", *_BLOCK])

    assert ui.errors == _BLOCK  # the sentence, every path -> target line, the clients line and the Fix line
    assert not any("Warning: noise" in line or "==> Phase" in line for line in ui.errors)


def test_the_failure_line_names_the_clients_that_hold_the_blocking_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = _run(monkeypatch, list(_BLOCK))

    (fail,) = ui.fails
    assert "Project update-project failed for Claude Code" in fail
    assert "Grok Build CLI" in fail and "Claude Code (.claude)" in fail


def test_a_plain_error_without_a_block_is_still_shown_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = _run(monkeypatch, ["Phase: x", "Error: update-project failed: OSError: boom", "trailing line"])

    assert "Error: update-project failed: OSError: boom" in ui.errors
    (fail,) = ui.fails
    assert "Clients affected" not in fail and "blocked by" not in fail


# ── Review: a long refusal keeps its header and Fix line, and every line is bounded and sanitised ──────────────

_KEY = "trw_dk_supersecretvalue0123456789abcdef"


def _long_block(paths: int) -> list[str]:
    lines = [_BLOCK[0]]
    lines += [f".grok/skills/third-party-{i} -> /real/elsewhere/third-party-{i}" for i in range(paths)]
    return [*lines, _BLOCK[3], _BLOCK[4]]


def test_a_long_refusal_keeps_the_header_and_the_fix_line_and_counts_what_it_omits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import tempfile

    def no_files(*_a: object, **_k: object) -> None:
        raise AssertionError("the relay must not create a temp file")

    monkeypatch.setattr(tempfile, "mkstemp", no_files)
    project = tmp_path / "my project"
    ui = _run(monkeypatch, _long_block(200), ["trw-mcp", "update-project", str(project)])

    assert ui.errors[0] == _BLOCK[0]  # the header survives
    assert ui.errors[-1] == _BLOCK[4]  # so does the Fix line
    assert ".grok/skills/third-party-0 -> " in ui.errors[1]  # the FIRST paths are kept, not the last
    assert sum(" -> /real/elsewhere/" in ln for ln in ui.errors) == 15
    assert _BLOCK[3] in ui.errors  # the clients line
    (marker,) = [ln for ln in ui.errors if ln.startswith("...and ")]
    assert marker == f"...and 185 more; `trw-mcp update-project --dry-run '{project}'` lists them all"


def test_a_short_refusal_prints_no_omission_line(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = _run(monkeypatch, _long_block(3))

    assert not [ln for ln in ui.errors if ln.startswith("...and ")]  # the current marker is absent
    for i in range(3):  # and all three paths are present
        assert any(f".grok/skills/third-party-{i} -> " in ln for ln in ui.errors)


def test_every_relayed_line_is_sanitised_and_the_one_total_budget_covers_everything_emitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block = _long_block(40)
    block[1] = f".grok/skills/a -> /real/{_KEY}/\x1b[2Jx\x07"  # a key-shaped token and control bytes in a path
    block[2] = ".grok/skills/b -> " + "a" * 1_000_000  # a megabyte line
    block[0] = "Error: " + "h" * 1_000_000  # a megabyte header
    block[-2] = "Clients affected: " + "c" * 1_000_000  # and clients line
    block[-1] = "Fix: " + "f" * 1_000_000  # and Fix line
    ui = _run(monkeypatch, block, ["trw-mcp", "update-project", "/" + "d" * 100_000])  # and a huge target dir

    text = "\n".join(ui.errors)
    assert _KEY not in text and "[redacted]" in text
    assert "\x1b" not in text and "\x07" not in text
    assert max(len(ln) for ln in ui.errors) <= 1200  # the per-line limit
    assert sum(len(ln) for ln in ui.errors) <= 8000  # the one total limit, markers included
    assert ui.errors[0].startswith("Error: ") and ui.errors[-1].startswith("Fix: ")  # reserved, never squeezed out
    assert any(ln.startswith("Clients affected:") for ln in ui.errors)
    assert any(ln.startswith("...and ") and "them all" in ln for ln in ui.errors)


def test_a_clients_line_is_sanitised_before_it_reaches_the_failure_message(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = _run(monkeypatch, [_BLOCK[0], f"Clients affected: Grok {_KEY}\x1b[31m (.grok/skills)", _BLOCK[4]])

    (fail,) = ui.fails
    assert _KEY not in fail and "\x1b" not in fail


def test_a_long_single_line_error_keeps_its_remedy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refusal sentence that ends in its remedy ('clean reinstall: `trw-mcp uninstall --keep-memory`') survives the clip."""
    line = "Error: refusing to update: " + "x" * 700 + " — clean reinstall: `trw-mcp uninstall --keep-memory`"
    ui = _run(monkeypatch, [line])

    assert "trw-mcp uninstall --keep-memory" in ui.errors[0]


def test_the_total_budget_binds_when_every_path_line_is_huge(monkeypatch: pytest.MonkeyPatch) -> None:
    block = [_BLOCK[0]]
    block += [f".grok/skills/p{i} -> /" + "x" * 1_000_000 for i in range(40)]
    block += [_BLOCK[3], _BLOCK[4]]
    ui = _run(monkeypatch, block, ["trw-mcp", "update-project", "/" + "d" * 100_000])

    assert sum(len(ln) for ln in ui.errors) <= 8000  # markers included
    shown = sum(" -> /" in ln for ln in ui.errors)
    assert 0 < shown < 15  # the budget, not the 15-path cap, decided
    (marker,) = [ln for ln in ui.errors if ln.startswith("...and ")]
    assert marker.startswith(f"...and {40 - shown} more;")  # the count is of what was actually left out
    assert ui.errors[0] == _BLOCK[0] and ui.errors[-1] == _BLOCK[4]


def test_the_closing_line_fits_inside_the_one_budget_even_when_the_paths_fill_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The room kept for the "...and N more" line is part of the budget, not on top of it."""
    block = [_BLOCK[0]] + [f".grok/skills/p{i} -> /" + "x" * 520 for i in range(40)] + [_BLOCK[3], _BLOCK[4]]
    ui = _run(monkeypatch, block, ["trw-mcp", "update-project", "/" + "d" * 400])

    assert sum(len(ln) for ln in ui.errors) <= 8000
    assert any(ln.startswith("...and ") for ln in ui.errors)


# ── Round 3: the recovery command is the real launcher's, quoted, and never cut ───────────────────────────────


def _marker(ui: _Ui) -> str:
    (marker,) = [ln for ln in ui.errors if ln.startswith("...and ")]
    return marker


@pytest.mark.parametrize(
    "launcher",
    [["trw-mcp"], ["/opt/py env/bin/python", "-B", "-m", "trw_mcp.server"]],
    ids=["console-script", "python-module"],
)
def test_the_recovery_command_uses_the_real_launcher(monkeypatch: pytest.MonkeyPatch, launcher: list[str]) -> None:
    import shlex

    ui = _run(monkeypatch, _long_block(40), [*launcher, "update-project", "/proj/my project", "--ide", "codex"])

    command = _marker(ui).split("`")[1]
    assert shlex.split(command) == [*launcher, "update-project", "--dry-run", "/proj/my project"]  # no --ide, no -m


def test_a_long_quoted_path_is_never_cut_inside_the_quoting(monkeypatch: pytest.MonkeyPatch) -> None:
    import shlex

    where = "/" + "d " * 400 + "end"  # 800+ characters with spaces, so it must be quoted
    ui = _run(monkeypatch, _long_block(40), ["trw-mcp", "update-project", where])

    assert shlex.split(_marker(ui).split("`")[1]) == ["trw-mcp", "update-project", "--dry-run", where]


def test_a_command_too_long_to_print_whole_is_left_out_rather_than_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = _run(monkeypatch, _long_block(40), ["trw-mcp", "update-project", "/" + "d" * 100_000])

    marker = _marker(ui)
    assert "`" not in marker and "update-project --dry-run in the project directory" in marker
    assert sum(len(ln) for ln in ui.errors) <= 8000


def test_the_budget_also_binds_an_error_that_does_not_start_a_line(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [f"update-project: warning then error {i} " + "e" * 1_000_000 for i in range(20)]
    ui = _run(monkeypatch, lines)

    assert ui.errors  # something is said
    assert len("\n".join(ui.errors)) <= 8000
    assert max(len(ln) for ln in ui.errors) <= 1200
