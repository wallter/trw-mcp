"""``trw_dispatch`` modes replace three helper tools (PRD-CORE-300-FR09, slice S7).

The dispatch status tool and the two work-evidence tools are now
``action="status"``, ``"evidence"`` and ``"validate_evidence"``. Behaviour per mode is covered where it always was
(``test_dispatch_tools.py``, ``test_agent_work_evidence_tool.py``); this file
pins the mode contract and the exposure flag.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP

from tests.conftest import extract_tool_fn
from trw_mcp.tools.dispatch import register_dispatch_tools

pytestmark = pytest.mark.unit


def _dispatch() -> Any:
    server = FastMCP("modes")
    register_dispatch_tools(server)
    return extract_tool_fn(server, "trw_dispatch")


def test_an_unknown_action_names_the_valid_ones() -> None:
    out = _dispatch()(action="launch-rockets")
    assert out["exit_code"] == 2
    for action in ("launch", "status", "evidence", "validate_evidence"):
        assert action in out["error"]


@pytest.mark.parametrize(
    ("action", "missing"), [("launch", "prompt"), ("status", "target"), ("validate_evidence", "target")]
)
def test_a_mode_without_its_required_argument_is_refused(action: str, missing: str) -> None:
    out = _dispatch()(action=action)
    assert out["exit_code"] == 2
    assert missing in out["error"]


def test_status_and_evidence_modes_work_inside_a_dispatched_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """The nested-launch guard refuses launching only; reading a job or validating evidence launches nothing."""
    monkeypatch.setenv("TRW_DISPATCH_CHILD", "1")
    dispatch = _dispatch()

    assert "nested dispatch" in dispatch(action="launch", prompt="x")["error"]
    assert dispatch(action="validate_evidence", target="{}")["valid"] is False
    assert "unknown job_id" in dispatch(action="status", target="ghost")["error"]


def test_the_three_helpers_are_not_registered() -> None:
    from tests.conftest import get_tools_sync

    server = FastMCP("modes")
    register_dispatch_tools(server)
    assert set(get_tools_sync(server)) == {"trw_dispatch"}


@pytest.mark.parametrize("comms_enabled", [False, True])
def test_trw_dispatch_is_exposed_only_by_dispatch_tools_exposed(comms_enabled: bool) -> None:
    """FR09: whatever comms_enabled is, only dispatch_tools_exposed puts trw_dispatch on the surface."""
    from trw_mcp.server._surface_manifest_registry import resolve_tool_surface

    for task in ("coding", "research", "unknown"):
        off = resolve_tool_surface(task, comms_enabled=comms_enabled, dispatch_enabled=False).tools
        on = resolve_tool_surface(task, comms_enabled=comms_enabled, dispatch_enabled=True).tools
        assert "trw_dispatch" not in off
        assert "trw_dispatch" in on


@pytest.mark.parametrize(("document", "error_type"), [("not json", "json_invalid"), ("[1, 2]", "dict_type")])
def test_validate_evidence_reports_a_bad_document_in_the_error_shape(document: str, error_type: str) -> None:
    out = _dispatch()(action="validate_evidence", target=document)
    assert out["valid"] is False
    assert out["errors"][0]["type"] == error_type
