"""PRD-CORE-233 NFR03 — promoting ``run_path`` must not weaken its containment.

FR01/FR03 tell every checkpoint-holding sub-agent to pass ``run_path=``. That
makes the containment check at the resolver a load-bearing security boundary
rather than an edge case: an escaping path must be refused BEFORE any write,
and the refusal must not echo an absolute path from outside the project root.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

_IGNORED_DIRS = ("logs", "runtime")


def _snapshot(root: Path) -> dict[str, str]:
    snap: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in _IGNORED_DIRS for part in rel.parts):
            continue
        snap[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snap


@pytest.fixture
def isolated_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Project root is a SUBDIRECTORY of tmp_path so ``tmp_path/outside-*`` escapes it.

    The conftest ``_isolate_trw_dir`` autouse fixture pins
    ``resolve_project_root`` to ``tmp_path`` itself, which would make every
    scratch path "inside the project" and render the containment assertions
    vacuous — so it is re-pointed here.
    """
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project_root))
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project_root)
    from trw_mcp.models.config import _reset_config, get_config

    _reset_config()
    from trw_mcp.state import _pin_store as pin_store_mod
    from trw_mcp.state._paths import _pinned_runs

    _pinned_runs.clear()
    pin_store_mod.invalidate_pin_store_cache()
    config = get_config()
    (project_root / config.runs_root).mkdir(parents=True, exist_ok=True)
    (project_root / config.trw_dir).mkdir(parents=True, exist_ok=True)
    return project_root


def _ctx(session_id: str) -> Any:
    from trw_mcp.state._paths import TRWCallContext

    return TRWCallContext(
        session_id=session_id,
        client_hint=None,
        explicit=False,
        fastmcp_session=None,
    )


def test_escaping_run_path_refused_before_write(isolated_project: Path, tmp_path: Path) -> None:
    """NFR03: an out-of-root run_path raises and writes nothing."""
    from trw_mcp.exceptions import StateError
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    outside = tmp_path / "outside-run"
    (outside / "meta").mkdir(parents=True)
    before_outside = _snapshot(outside)
    before_project = _snapshot(isolated_project)

    with pytest.raises(StateError, match="escapes project root"):
        execute_checkpoint(str(outside), "exfiltrate", None, None, context=_ctx("nfr03"))

    assert _snapshot(outside) == before_outside
    assert _snapshot(isolated_project) == before_project


def test_escaping_run_path_rejection_does_not_echo_the_outside_path(
    isolated_project: Path,
    tmp_path: Path,
) -> None:
    """NFR03: the caller-visible message must not carry a path outside the root."""
    from trw_mcp.exceptions import StateError
    from trw_mcp.state._paths import resolve_run_path

    outside = tmp_path / "outside-run"
    outside.mkdir(parents=True)

    with pytest.raises(StateError) as exc_info:
        resolve_run_path(str(outside), context=_ctx("nfr03-echo"))

    message = str(exc_info.value)
    assert "escapes project root" in message
    assert str(outside) not in message, f"message echoed an out-of-root path: {message!r}"


def test_escaping_run_path_is_refused_even_when_it_does_not_exist(
    isolated_project: Path,
    tmp_path: Path,
) -> None:
    """Containment is checked BEFORE existence, so no path outside the root leaks."""
    from trw_mcp.exceptions import StateError
    from trw_mcp.state._paths import resolve_run_path

    outside = tmp_path / "does-not-exist"

    with pytest.raises(StateError) as exc_info:
        resolve_run_path(str(outside), context=_ctx("nfr03-missing"))

    message = str(exc_info.value)
    assert "escapes project root" in message
    assert str(outside) not in message


def test_escaping_run_path_is_not_softened_into_a_not_recorded_result(
    isolated_project: Path,
    tmp_path: Path,
) -> None:
    """FR02's softening must never leak into the invalid-path branch."""
    from trw_mcp.exceptions import StateError
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    outside = tmp_path / "outside-run-2"
    outside.mkdir(parents=True)

    with pytest.raises(StateError):
        execute_checkpoint(str(outside), "quiet failure?", None, None, context=_ctx("nfr03-soft"))


def test_in_project_run_path_still_resolves(isolated_project: Path) -> None:
    """Non-vacuity: containment refuses escapes, not legitimate in-project paths."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_run_path

    run_dir = isolated_project / get_config().runs_root / "task-a" / "run-1"
    (run_dir / "meta").mkdir(parents=True)

    assert resolve_run_path(str(run_dir), context=_ctx("nfr03-ok")) == run_dir.resolve()
