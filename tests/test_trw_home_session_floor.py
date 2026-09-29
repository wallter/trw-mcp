"""The session floor in ``tests/_trw_home.py``: fixtures scoped wider than a test never see the real HOME.

A module-scoped fixture is set up before the function-scoped ``isolated_trw_home``; before the session
floor it ran with the process HOME, and ``test_bootstrap_manifest_ownership.py``'s module fixture wrote the
operator's real ``~/.gemini/config/mcp_config.json`` through it (2026-09-29).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests._trw_home import _REAL_HOME

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def module_scoped_home() -> dict[str, str]:
    return {key: os.environ[key] for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "TRW_USER_DIR")}


def test_a_module_scoped_fixture_runs_under_the_session_home_not_the_real_one(
    module_scoped_home: dict[str, str],
) -> None:
    home = Path(module_scoped_home["HOME"])
    assert home != _REAL_HOME
    assert home.name.startswith("trw-session-home")
    for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "TRW_USER_DIR"):
        assert Path(module_scoped_home[key]).is_relative_to(home), key


def test_each_test_still_gets_its_own_home_on_top_of_the_session_one(module_scoped_home: dict[str, str]) -> None:
    assert Path(os.environ["HOME"]).name.startswith("trw-home")
    assert os.environ["HOME"] != module_scoped_home["HOME"]
