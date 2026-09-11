"""Project-wide ceremony summaries must not pose as current-run evidence."""

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.ceremony_progress import (
    CeremonyState,
    mark_session_started,
    read_ceremony_state,
    write_ceremony_state,
)
from trw_mcp.tools import _ceremony_status


@pytest.mark.unit
def test_prior_session_success_is_labeled_aggregate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Use real persistence/startup: a new session inherits project summaries."""
    trw_dir = tmp_path / ".trw"
    write_ceremony_state(
        trw_dir,
        CeremonyState(
            session_started=True,
            checkpoint_count=10,
            build_check_result="passed",
            session_build_results={"prior": "passed"},
            review_called=True,
            review_verdict="pass",
            deliver_called=True,
        ),
    )
    mark_session_started(trw_dir, session_id="new")
    state = read_ceremony_state(trw_dir)
    assert state.session_build_results["new"] == "pending"
    monkeypatch.setattr(_ceremony_status, "_load_config_for_trw_dir", lambda _: TRWConfig(nudge_enabled=False))
    response = _ceremony_status.append_ceremony_status({"recorded": True}, trw_dir)
    status = str(response["ceremony_status"])
    assert "build=passed" in status and "deliver_called" in status
    assert "scope=project_aggregate" in status
    assert "not current-run evidence" in status
    assert response["recorded"] is True
