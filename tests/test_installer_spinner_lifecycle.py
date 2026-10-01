"""The installer never leaves a spinner thread repainting over a prompt (operator feedback 2026-10-01 12:59Z).

A failed proprietary step left its spinner running (the ``except RuntimeError`` branches never stopped it), and
``start_spinner`` replaced ``self._spinner`` without stopping the old thread. Five leaked daemon threads kept
repainting the last line, erased the "Install semantic-embeddings stack?" prompt within 0.1s, and the install
looked hung. One spinner at a time; every verdict line and every prompt stops a live spinner first.
"""

from __future__ import annotations

import io
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tests._install_trw_pip_target_contract_support import _INSTALLER_TEMPLATE, _load_installer_module


def _spinner_threads() -> int:
    return sum(1 for t in threading.enumerate() if t.is_alive() and getattr(t, "_target", None) is not None
               and getattr(t._target, "__name__", "") == "_run")  # fmt: skip


class _Tty(io.StringIO):
    def close(self) -> None:  # the prompt closes its tty; keep the buffer readable
        pass


@pytest.fixture
def installer() -> Any:
    return _load_installer_module(_INSTALLER_TEMPLATE)


def _prompt_survives(installer: Any, capsys: pytest.CaptureFixture[str]) -> None:
    capsys.readouterr()
    # Fake only the terminal file, and only for the prompt, so the real _open_tty (where the halt lives) runs.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(installer, "open", lambda *_a, **_k: _Tty("n\n"), raising=False)
        installer.prompt_yes_no("Install semantic-embeddings stack?", default="y")
    time.sleep(0.35)  # long enough for any leaked spinner to repaint (it repaints every 0.1s)
    out = capsys.readouterr().out
    assert "Install semantic-embeddings stack?" in out
    assert out.rstrip().endswith("[Y/n]"), f"a spinner repainted over the prompt: {out!r}"


def test_a_failed_proprietary_step_leaves_no_spinner_over_the_next_prompt(
    installer: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _spinner_threads()

    def _refuse(*_a: object, **_k: object) -> dict[str, Any]:
        raise RuntimeError("entitlement refused")

    monkeypatch.setattr(installer, "_post_proprietary_entitlement", _refuse)
    ui = installer.UI(interactive=True)
    installer.phase_install_proprietary(
        ui, 1, 1, "python3", "key", {}, "https://example.invalid", auto_confirm=True, project_dir=tmp_path
    )
    assert _spinner_threads() == baseline
    _prompt_survives(installer, capsys)
    assert _spinner_threads() == baseline


def test_starting_a_spinner_stops_the_running_one(installer: Any) -> None:
    baseline = _spinner_threads()
    ui = installer.UI(interactive=True)
    ui.start_spinner("first")
    ui.start_spinner("second")
    ui.stop_spinner(True, "done")
    assert _spinner_threads() == baseline


def test_a_prompt_stops_a_spinner_nobody_stopped(installer: Any, capsys: pytest.CaptureFixture[str]) -> None:
    baseline = _spinner_threads()
    installer.UI(interactive=True).start_spinner("left running by a path that forgot to stop it")
    _prompt_survives(installer, capsys)
    assert _spinner_threads() == baseline
