"""Fresh-process import-order contracts for models and scoring."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "script",
    [
        "import trw_mcp.scoring",
        (
            "from trw_mcp.models import resolve_task_profile as facade; "
            "from trw_mcp.models.task_profile import resolve_task_profile as direct; "
            "import trw_mcp.scoring; assert facade is direct"
        ),
        (
            "import trw_mcp.scoring; "
            "from trw_mcp.models import resolve_task_profile as facade; "
            "from trw_mcp.models.task_profile import resolve_task_profile as direct; "
            "assert facade is direct"
        ),
        (
            "from trw_mcp.models import *; "
            "from trw_mcp.models.task_profile import resolve_task_profile as direct; "
            "assert resolve_task_profile is direct"
        ),
    ],
)
def test_models_and_scoring_cold_import_orders(script: str) -> None:
    package_src = Path(__file__).parents[1] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(package_src), env.get("PYTHONPATH", "")))

    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
