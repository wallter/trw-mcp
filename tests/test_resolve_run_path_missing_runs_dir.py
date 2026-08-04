"""A missing runs/ directory must yield the SAME typed answer as "no active run" (audit C-3).

``resolve_run_path`` has two paths that mean the same thing to a caller:

  * the runs root does not exist at all;
  * the runs root exists but holds no run this session may claim.

The second carried the full recovery contract — ``suggestion=`` plus the
machine-readable ``reason`` marker PRD-CORE-233 FR02 keys on to build its
remedy. The first raised a bare sentence with neither, so a caller that hit it
got a dead end and FR02's handling was bypassed entirely.

Every existing fixture builds a ``.trw/`` tree with ``runs/`` already present,
so nothing exercised the missing-directory branch — the fixture masked it.
These tests deliberately do NOT create ``runs/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.exceptions import StateError
from trw_mcp.state._no_active_run import NO_ACTIVE_RUN_REASON
from trw_mcp.state._paths import resolve_run_path


@pytest.fixture
def project_without_runs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project root whose .trw/ exists but which has NO runs/ directory."""
    (tmp_path / ".trw").mkdir()
    assert not (tmp_path / ".trw" / "runs").exists(), "fixture precondition"
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: tmp_path)
    return tmp_path


@pytest.mark.unit
def test_missing_runs_dir_carries_the_fr02_reason_marker(
    project_without_runs_dir: Path,
) -> None:
    """FR02 keys on ``reason``; without it the remedy machinery never engages."""
    with pytest.raises(StateError) as excinfo:
        resolve_run_path(None)

    # TRWError funnels **context kwargs into `.context`, not onto attributes.
    assert excinfo.value.context.get("reason") == NO_ACTIVE_RUN_REASON, (
        "a missing runs/ directory must be reported as the no-active-run condition, "
        "not as an untyped error FR02 cannot recognise"
    )


@pytest.mark.unit
def test_missing_runs_dir_offers_a_remedy(project_without_runs_dir: Path) -> None:
    """The caller must be told what to do, not just what went wrong."""
    with pytest.raises(StateError) as excinfo:
        resolve_run_path(None)

    suggestion = excinfo.value.suggestion
    assert suggestion, "the error must carry an actionable suggestion"
    assert str(suggestion) in str(excinfo.value), (
        "the remedy must also appear in the human-readable message — a caller "
        "reading only the message text should not hit a dead end"
    )


@pytest.mark.unit
def test_missing_runs_dir_still_names_the_actual_cause(
    project_without_runs_dir: Path,
) -> None:
    """Adding the remedy must not erase the diagnostic detail."""
    with pytest.raises(StateError) as excinfo:
        resolve_run_path(None)

    message = str(excinfo.value)
    assert "not found" in message
    assert "runs" in message


@pytest.mark.unit
def test_absent_and_empty_runs_dir_agree_on_the_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The two spellings of "no run available" must not diverge.

    This is the property the fix restores: whether ``runs/`` is missing or
    merely empty is an implementation detail of the filesystem, never a
    difference the caller should have to handle two ways.
    """
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: tmp_path)
    (tmp_path / ".trw").mkdir()

    with pytest.raises(StateError) as absent:
        resolve_run_path(None)

    (tmp_path / ".trw" / "runs").mkdir()
    with pytest.raises(StateError) as empty:
        resolve_run_path(None)

    assert absent.value.context.get("reason") == empty.value.context.get("reason")
    assert absent.value.context.get("reason") == NO_ACTIVE_RUN_REASON
