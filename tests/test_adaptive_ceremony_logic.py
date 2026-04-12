import pytest
from pathlib import Path
from trw_mcp.state._ceremony_progress_state import CeremonyState, write_ceremony_state
from trw_mcp.tools._ceremony_status import append_ceremony_status
from trw_mcp.models.config import TRWConfig, get_config
from unittest.mock import patch, MagicMock

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
    (trw_dir / "runs").mkdir()
    (trw_dir / "runs" / "test-run").mkdir(parents=True)
    (trw_dir / "runs" / "test-run" / "meta").mkdir()
    with open(trw_dir / "runs" / "test-run" / "meta" / "run.yaml", "w") as f:
        f.write("complexity_class: MINIMAL\n")
    
    with patch("trw_mcp.state._paths.resolve_run_path", return_value=trw_dir / "runs" / "test-run"):
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
