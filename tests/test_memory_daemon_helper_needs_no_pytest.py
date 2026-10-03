"""``tests._memory_daemon`` is imported by ``benchmarks/engmem_mcp.py``, which the canary memory-soak runs under the canary venv's interpreter (``-I``): a venv has no pytest.

The helper therefore must import and run its daemon context without pytest (the memory-soak's recall/session_start/engmem rows were all ``not_measured`` with
``ModuleNotFoundError: No module named 'pytest'`` until it did).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TESTS_PARENT = Path(__file__).resolve().parents[1]


def test_the_daemon_helper_imports_with_pytest_unavailable() -> None:
    code = "import sys; sys.modules['pytest'] = None; sys.path.insert(0, sys.argv[1]); import tests._memory_daemon as m; print(m.running_daemon.__name__)"
    done = subprocess.run(
        [sys.executable, "-c", code, str(TESTS_PARENT)], capture_output=True, text=True, check=False, cwd=TESTS_PARENT
    )
    assert done.returncode == 0, done.stderr[-600:]
    assert done.stdout.strip() == "running_daemon"


def test_the_engmem_harness_imports_with_pytest_unavailable() -> None:
    """The soak's real entry: every ``tests.*`` name ``benchmarks/engmem_mcp.py`` pulls in comes from a pytest-free module."""
    code = (
        "import sys; sys.modules['pytest'] = None; sys.path.insert(0, sys.argv[1]);"
        " from tests._memory_daemon import MemoryDaemon, attach_checkout, running_daemon;"
        " print(MemoryDaemon.__name__, attach_checkout.__name__)"
    )
    done = subprocess.run(
        [sys.executable, "-c", code, str(TESTS_PARENT)], capture_output=True, text=True, check=False, cwd=TESTS_PARENT
    )
    assert done.returncode == 0, done.stderr[-600:]
    assert done.stdout.split() == ["MemoryDaemon", "attach_checkout"]


def test_the_fixtures_module_still_reexports_the_moved_names() -> None:
    from tests import _memory_daemon, _memory_fixtures

    assert _memory_fixtures.MemoryDaemon is _memory_daemon.MemoryDaemon
    assert _memory_fixtures.attach_checkout is _memory_daemon.attach_checkout
