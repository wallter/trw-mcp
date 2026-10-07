"""audit and export read another checkout without moving this process's project (PRD-CORE-305 FR07 row c).

They used to set process-wide ``TRW_PROJECT_ROOT`` and reload the shared config around each read, so
every other thread in the process resolved the audited checkout as its own project until the read
finished, and the config singleton other threads hold was thrown away twice. They now bind the target
to their own context (``project_bound``).
"""

from __future__ import annotations

import ast
import os
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from tests import _source_index as source_index
from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.state._paths import resolve_project_root


def _entry_points(target: Path) -> dict[str, tuple[str, Callable[[], object]]]:
    """``{name: (module attribute the entry point reads under its binding, call)}``."""
    from trw_mcp import audit, export

    trw_dir = target / ".trw"
    return {
        "audit ceremony compliance": ("trw_mcp.audit.scan_all_runs", lambda: audit._audit_ceremony_compliance(target)),
        "audit reflection quality": (
            "trw_mcp.audit.compute_reflection_quality",
            lambda: audit._audit_reflection_quality(trw_dir),
        ),
        "export runs": ("trw_mcp.export.scan_all_runs", lambda: export._collect_runs(target)),
        "export reflection quality": (
            "trw_mcp.export.compute_reflection_quality",
            lambda: export._collect_analytics(target, trw_dir, TRWConfig()),
        ),
    }


@pytest.mark.parametrize(
    "name", ["audit ceremony compliance", "audit reflection quality", "export runs", "export reflection quality"]
)
def test_read_resolves_target_while_process_keeps_its_own_project(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, target = tmp_path / "home", tmp_path / "audited"
    for project in (home, target):
        (project / ".trw").mkdir(parents=True)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(home))
    process_config = get_config()
    seen: dict[str, object] = {}

    def probe(*_args: object, **_kwargs: object) -> dict[str, object]:
        other: list[Path] = []
        worker = threading.Thread(target=lambda: other.append(resolve_project_root()))
        worker.start()
        worker.join()
        seen.update(inside=resolve_project_root(), env=os.environ.get("TRW_PROJECT_ROOT"), other_thread=other[0])
        seen["process_config_kept"] = _process_singleton() is process_config
        return {}

    attribute, call = _entry_points(target)[name]
    monkeypatch.setattr(attribute, probe)
    call()

    assert seen["inside"] == target.resolve()
    assert seen["env"] == str(home), "the process-wide TRW_PROJECT_ROOT moved"
    assert seen["other_thread"] == home.resolve(), "another thread resolved the audited checkout"
    assert seen["process_config_kept"], "the process config singleton was dropped during the read"
    assert get_config() is process_config, "the process config singleton was dropped after the read"


def _process_singleton() -> TRWConfig | None:
    from trw_mcp.models.config import _loader

    return _loader._singleton


def test_project_bound_keeps_no_daemon_budget_unless_inside_an_install(tmp_path: Path) -> None:
    from trw_mcp.state._project_root_binding import install_shared, installing_into, project_bound

    with project_bound(tmp_path):
        assert install_shared() is None  # an export's daemon waits stay unbounded
    with installing_into(tmp_path / "install"):
        budget = install_shared()
        assert budget is not None
        with project_bound(tmp_path):
            assert install_shared() is budget  # nested in an install, it draws on the install's budget
        with installing_into(tmp_path / "nested"):
            assert install_shared() is budget


def test_no_shipped_code_assigns_the_process_wide_project_root() -> None:
    """Census: naming a project for a block is ``project_bound``/``installing_into``, never the environment."""
    src = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"
    writes: list[str] = []
    for path in src.rglob("*.py"):
        for node in ast.walk(source_index.tree(path)):
            targets = node.targets if isinstance(node, ast.Assign) else []
            for target in targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "TRW_PROJECT_ROOT"
                    and ast.unparse(target.value) in {"os.environ", "environ"}
                ):
                    writes.append(f"{path.relative_to(src)}:{node.lineno}")
            if (
                isinstance(node, ast.Call)
                and ast.unparse(node.func) in {"os.environ.pop", "os.putenv", "os.environ.setdefault", "os.unsetenv"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "TRW_PROJECT_ROOT"
            ):
                writes.append(f"{path.relative_to(src)}:{node.lineno}")
    assert not writes, f"process-wide TRW_PROJECT_ROOT writes (use project_bound): {writes}"
