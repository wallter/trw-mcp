"""A child process started in another directory imports the checkout under test, not the shared .venv's."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import trw_mcp

pytestmark = pytest.mark.integration


def test_a_child_in_another_cwd_imports_this_checkout(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", "import trw_mcp; print(trw_mcp.__file__)"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=True,
    )
    assert Path(proc.stdout.strip()).resolve() == Path(trw_mcp.__file__).resolve()
