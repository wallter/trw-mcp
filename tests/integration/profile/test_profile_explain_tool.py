"""FR-11 — trw_profile_explain MCP tool contract test."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.conftest import extract_tool_fn, make_test_server


def _explain_fn() -> Any:
    server = make_test_server()
    from trw_mcp.tools.trw_profile_explain import register_trw_profile_explain_tools

    register_trw_profile_explain_tools(server)
    return extract_tool_fn(server, "trw_profile_explain")


def test_profile_explain_registered_and_returns_payload(tmp_path: Path) -> None:
    """FR-11: the tool registers and returns the structured explain payload."""
    fn = _explain_fn()
    payload: dict[str, Any] = fn()
    assert "fields" in payload
    assert "layers_applied" in payload
    # Each field record carries the attribution contract.
    for record in payload["fields"]:
        assert set(record) == {"field", "value", "origin_layer", "override_chain"}


def test_profile_explain_honors_explicit_domain(tmp_path: Path) -> None:
    """FR-11: an explicit domain flag reaches the resolver and discovery."""
    profiles = tmp_path / ".trw" / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    (profiles / "domain-frontend.yaml").write_text("build_check_scope: targeted\n", encoding="utf-8")
    fn = _explain_fn()
    payload: dict[str, Any] = fn(domain="frontend")
    assert "domain" in payload["layers_applied"]
    bcs = next(f for f in payload["fields"] if f["field"] == "build_check_scope")
    assert bcs["origin_layer"] == "domain"
    assert bcs["value"] == "targeted"
