"""The shared conftest must not import fastmcp (~0.85 s) on every run of every test file."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _run(code: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=_ROOT, capture_output=True, text=True, check=True, timeout=120
    )
    return result.stdout.strip()


def test_importing_the_conftest_does_not_import_fastmcp() -> None:
    assert _run("import sys, tests.conftest; print('fastmcp' in sys.modules)") == "False"


def test_make_test_server_imports_fastmcp_and_builds_a_server() -> None:
    out = _run(
        "import sys, tests.conftest as c; s = c.make_test_server(); print(type(s).__name__, 'fastmcp' in sys.modules)"
    )
    assert out == "FastMCP True"
