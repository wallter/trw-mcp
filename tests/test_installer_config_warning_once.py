"""A retired-config-key warning prints once per installer run, not once per client (E2E row 10).

`update-project` runs once per configured client, each a fresh process, so the per-process dedup in
`_retired_keys._WARNED` cannot span them. The installer already drops a repeated `WARNING:` notice
(`UI.first_sighting`); the config warnings use the `TRW: WARNING — ` prefix the relay did not recognise.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"
_RETIRED = (
    "import sys; print(\"TRW: WARNING \\u2014 .trw/config.yaml sets 'claude_md_max_lines', which has no effect: "
    "it was retired and has no replacement.\", file=sys.stderr); print('Updated: a-file')"
)
_CHILD = [sys.executable, "-c", _RETIRED]


@pytest.fixture
def installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_config_warning_once", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_headless_mode_prints_the_retired_key_warning_once_across_clients(
    installer: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    ui = installer.UI(interactive=False)

    for _client in range(4):
        assert installer.run_with_progress(ui, "Updating", _CHILD)

    out = capsys.readouterr().out
    assert out.count("claude_md_max_lines") == 1, out
    assert out.count("Updated: a-file") == 4, "ordinary progress lines are not deduplicated"


def test_interactive_mode_shows_the_retired_key_warning_once_across_clients(
    installer: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    ui = installer.UI(interactive=True)

    for _client in range(4):
        assert installer.run_with_progress(ui, "Updating", _CHILD)
        ui.stop_spinner(True, "configured")

    out = capsys.readouterr().out
    assert out.count("claude_md_max_lines") == 1, out


def test_a_different_config_warning_is_still_shown(installer: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    ui = installer.UI(interactive=False)
    other = [
        sys.executable,
        "-c",
        "import sys; print(\"TRW: WARNING \\u2014 .trw/config.yaml sets 'other_key', which has no effect: typo.\")",
    ]

    assert installer.run_with_progress(ui, "Updating", _CHILD)
    assert installer.run_with_progress(ui, "Updating", other)

    out = capsys.readouterr().out
    assert "claude_md_max_lines" in out and "other_key" in out
