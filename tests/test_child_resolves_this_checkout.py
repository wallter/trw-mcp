"""A child started with a restricted environment imports the same ``trw_mcp`` as the test process.

The shared venv installs ``trw_mcp`` editable from the MAIN checkout. A child spawned with an env that drops
``PYTHONPATH`` (``{"PATH", "HOME", ...}``) therefore runs main's tree while its parent runs the lane's, so the
test can pass against code it never ran. ``tests._layout.subprocess_pythonpath()`` is the one way to give such a
child this checkout's sources; this pins that it does.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import trw_memory

import trw_mcp
from tests._layout import MONOREPO_ROOT, subprocess_pythonpath

_CODE = "import trw_mcp, trw_memory; print(trw_mcp.__file__); print(trw_memory.__file__)"


def test_a_restricted_env_child_resolves_the_same_trw_mcp_as_the_parent() -> None:
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG") if key in os.environ}
    env["PYTHONPATH"] = subprocess_pythonpath()

    done = subprocess.run(
        [sys.executable, "-c", _CODE], env=env, capture_output=True, text=True, timeout=60, check=True
    )

    child_mcp, child_memory = (Path(line).resolve() for line in done.stdout.splitlines())
    assert child_mcp == Path(trw_mcp.__file__).resolve()
    if MONOREPO_ROOT is not None:  # the public package-alone checkout has no sibling src to pin
        assert child_memory == Path(trw_memory.__file__).resolve()
