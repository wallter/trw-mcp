"""PRD-FIX-141-FR07 — ``trw_init`` creates the ``TASK_DIR`` it advertises.

``trw_init`` writes ``variables.TASK_DIR`` into ``meta/run.yaml`` and every
agent reads it as the destination for the run's deliverables (``FRAMEWORK.md``
names it a write-scope boundary). It created nothing there, so the first write
into it failed and the operator had to ``mkdir`` by hand — a run record naming a
path that does not exist is a self-report that is false the moment it is
written.

These assertions stat the filesystem rather than reading the echoed result, so
they cannot be satisfied by a string the tool merely returned.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests._tools_orchestration_support import orch_tools, set_project_root  # noqa: F401
from trw_mcp.state.persistence import FileStateReader


def _task_dir(result: dict[str, str]) -> Path:
    run_yaml = FileStateReader().read_yaml(Path(result["run_path"]) / "meta" / "run.yaml")
    variables = run_yaml["variables"]
    assert isinstance(variables, dict)
    return Path(str(variables["TASK_DIR"]))


def test_init_creates_the_task_dir_named_in_run_yaml(orch_tools: dict[str, Any]) -> None:
    """The directory named by ``variables.TASK_DIR`` exists after init."""
    result = orch_tools["trw_init"].fn(task_name="taskdirdefault")

    task_dir = _task_dir(result)
    assert task_dir.is_dir(), f"run.yaml advertises TASK_DIR={task_dir} but nothing was created"


def test_init_creates_the_task_dir_under_an_overridden_task_root(orch_tools: dict[str, Any]) -> None:
    """An overridden ``task_root`` is created too, not just the default ``docs``."""
    result = orch_tools["trw_init"].fn(task_name="taskdiroverride", advanced={"task_root": "specs"})

    task_dir = _task_dir(result)
    assert task_dir.is_dir()
    assert task_dir.name == "taskdiroverride"
    assert task_dir.parent.name == "specs"


def test_init_is_idempotent_over_an_existing_task_dir(orch_tools: dict[str, Any], set_project_root: Path) -> None:
    """A pre-existing task directory (with content) is never clobbered."""
    existing = set_project_root / "docs" / "taskdirexisting"
    existing.mkdir(parents=True)
    keeper = existing / "NOTES.md"
    keeper.write_text("operator content", encoding="utf-8")

    result = orch_tools["trw_init"].fn(task_name="taskdirexisting")

    assert _task_dir(result) == existing
    assert keeper.read_text(encoding="utf-8") == "operator content"
