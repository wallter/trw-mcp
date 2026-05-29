from unittest.mock import patch

import pytest

from trw_mcp.state._ceremony_progress_state import CeremonyState, write_ceremony_state
from trw_mcp.tools._ceremony_status import append_ceremony_status


@pytest.fixture
def trw_dir(tmp_path):
    d = tmp_path / ".trw"
    d.mkdir()
    (d / "context").mkdir()
    return d


def test_adaptive_ceremony_minimal_suppression(trw_dir):
    """PRD-CORE-134-FR03: Suppress nudges for MINIMAL runs."""
    state = CeremonyState(phase="implement")
    write_ceremony_state(trw_dir, state)

    # Mock run.yaml to show MINIMAL complexity
    run_dir = trw_dir / "runs" / "test-run"
    (run_dir / "meta").mkdir(parents=True)
    with open(run_dir / "meta" / "run.yaml", "w") as f:
        f.write("complexity_class: MINIMAL\n")

    # PRD-FIX-085: TRWConfig.active_run_complexity is now PIN-ONLY — it reads
    # the run via get_pinned_run(), not the old resolve_run_path() seam (which
    # this test used to mock). Mocking the obsolete seam left the complexity
    # resolution dependent on whatever live pin existed in the dev repo's
    # .trw/runtime/pins.json, making suppression non-deterministic. Patch the
    # real seam so the MINIMAL run is resolved deterministically.
    with patch("trw_mcp.state._paths.get_pinned_run", return_value=run_dir):
        response = {"status": "ok"}
        result = append_ceremony_status(response, trw_dir=trw_dir)

        assert "ceremony_status" in result
        assert "nudge_content" not in result


def test_nudge_pool_selection_wired(trw_dir):
    """PRD-CORE-134-FR01: Verify pool selection is wired into live path."""
    state = CeremonyState(phase="implement", session_started=True)
    write_ceremony_state(trw_dir, state)

    # Force a specific pool via mock
    with patch("trw_mcp.state.ceremony_nudge._select_nudge_pool", return_value="workflow"):
        with patch("trw_mcp.state._nudge_content.load_pool_message", return_value="Test Workflow Nudge"):
            response = {"status": "ok"}
            result = append_ceremony_status(response, trw_dir=trw_dir)

            assert result.get("nudge_content") == "Test Workflow Nudge"


def test_nudge_fatigue_increment(trw_dir):
    """PRD-CORE-134-FR04: Tool call counter increments."""
    state = CeremonyState()
    write_ceremony_state(trw_dir, state)

    append_ceremony_status({"status": "ok"}, trw_dir=trw_dir)

    from trw_mcp.state._ceremony_progress_state import read_ceremony_state

    new_state = read_ceremony_state(trw_dir)
    assert new_state.tool_call_counter == 1

    append_ceremony_status({"status": "ok"}, trw_dir=trw_dir)
    new_state = read_ceremony_state(trw_dir)
    assert new_state.tool_call_counter == 2
