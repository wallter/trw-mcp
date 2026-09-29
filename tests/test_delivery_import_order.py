"""The delivery modules import cleanly in any order, in a fresh interpreter.

``_deliver_gate_selfcomputed.evaluate_build_authority`` imports
``_delivery_event_checks`` lazily inside a fail-OPEN handler. While
``_delivery_event_checks`` and ``_delivery_helpers`` imported each other, a
process that loaded ``_delivery_event_checks`` first hit an ImportError there,
the handler logged ``deliver_build_authority_degraded`` and returned "not
blocked": a failed trw_build_check could pass the deliver gate. Each case runs
in its own interpreter because an earlier import in the test process would
mask the cycle.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.mark.parametrize(
    "first",
    [
        "trw_mcp.tools._delivery_event_checks",
        "trw_mcp.tools._delivery_helpers",
        "trw_mcp.tools._deliver_gate_selfcomputed",
    ],
)
def test_a_delivery_module_imports_first_in_a_fresh_interpreter(first: str) -> None:
    probe = f"import {first}; import trw_mcp.tools._delivery_event_checks as m; m.latest_build_check_failed_for_run"
    done = subprocess.run(
        [sys.executable, "-c", probe],
        # This package first, then the test process's own path, so sibling packages (trw_memory) resolve as here.
        env={"PYTHONPATH": os.pathsep.join([str(_SRC), *filter(None, sys.path)]), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=False, timeout=120,
    )  # fmt: skip

    assert done.returncode == 0, done.stderr[-2000:]
