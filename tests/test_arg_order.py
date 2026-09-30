"""Explicit test-file arguments in ANY order keep every directory's conftest (COMMS-CONFTEST-ORDER).

pytest 9 matches a conftest's fixtures to the conftest's Package node OBJECT (``_matchfactories``:
``fixturedef.node in parent_nodes``). Arguments that leave a directory and come back
(``tests/comms/a.py tests/b.py tests/comms/c.py``) make collection build a second Package node for
``tests/comms``, so ``c.py`` loses that conftest: its fixtures are "not found", and its autouse
fixtures silently do not run. ``tests/_arg_order.py`` regroups the arguments by directory first.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests._arg_order import group_by_directory

PACKAGE = Path(__file__).resolve().parents[1]


def test_files_of_one_directory_become_contiguous_in_first_seen_order() -> None:
    args = ["tests/comms/a.py", "tests/b.py", "tests/comms/c.py::t", "tests/plan/d.py", "tests/e.py"]
    assert group_by_directory(args, PACKAGE) == [
        "tests/comms/a.py",
        "tests/comms/c.py::t",
        "tests/b.py",
        "tests/plan/d.py",
        "tests/e.py",
    ]


def test_an_already_grouped_order_is_left_as_given() -> None:
    args = ["tests/b.py", "tests/comms/c.py", "tests/comms/a.py", "tests/e.py"]
    assert group_by_directory(args, PACKAGE) == args


@pytest.mark.integration
@pytest.mark.duration_exempt("runs the three-file pytest order the bug needs in a subprocess (~6 s)")
def test_the_reported_three_file_order_keeps_the_comms_conftest() -> None:
    files = [
        "tests/comms/test_coordination_root.py",
        "tests/test_checkout_access_census.py",
        "tests/comms/test_authority_separation.py",
    ]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "0", *files],
        cwd=PACKAGE,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert "fixture 'comms_server' not found" not in result.stdout, result.stdout[-3000:]
    assert result.returncode == 0, result.stdout[-3000:]
