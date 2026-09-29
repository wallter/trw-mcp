"""Admission record for the opt-in shared trw-mcp server (``trw_mcp.shared_server``).

Belongs to the ``_field_admission_registry.py`` data table. One nested field
carries every knob, so the feature spends one slot of the public-field budget.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

SHARED_MCP_ADMISSIONS: dict[str, ConfigAdmission] = {
    "shared_mcp": ConfigAdmission(
        field_name="shared_mcp",
        owner="shared-mcp-proxy (operator request 2026-09-26)",
        consumer=(
            "trw_mcp.shared_server._server.serve_shared, trw_mcp.shared_server._cli.main_proxy and "
            "trw_mcp.shared_server._ops (swap, env create, status, doctor row)"
        ),
        default_rationale=(
            "Defaults to enabled=False: nothing changes until an operator opts in, and a client "
            "configured to launch `trw-mcp-proxy` runs the ordinary stdio server in-process while it "
            "stays off. max_inflight=256 bounds concurrent requests; idle_shutdown_seconds=3600 frees an "
            "unused env's memory; wheelhouse and envs_dir name the local-only paths swap and env use."
        ),
        interaction_analysis=(
            "Read only by the shared-server package and the proxy's enable check. max_inflight turns "
            "overload into an explicit busy answer the proxy resends with backoff and then reports; "
            "idle_shutdown_seconds lets an env's server exit and restart lazily on the next call."
        ),
        deprecation_plan="Retain while the shared topology is opt-in; revisit if it becomes the default.",
        docs_pointer="agent-docs/mcp-server.md",
        test_pointer="trw-mcp/tests/shared_server/test_shared_ops.py::test_disabled_proxy_runs_stdio_in_process",
        budget_decision="admitted",
    ),
}

__all__ = ["SHARED_MCP_ADMISSIONS"]
