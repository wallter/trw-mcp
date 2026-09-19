"""Admission records for the PRD-CORE-281 dispatch-access fields.

Its own table module rather than two more entries in
``_field_admission_registry.py``: that file sits close to the 350 effective-LOC
gate, so every domain that admits fields brings its own table and the registry
merges it (the pattern PRD-FIX-123 established).

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.

Both fields default FALSE. That is not caution for its own sake: each one opens
a hole in a stated containment boundary (the bounded CORE-218 tool surface, and
the dispatch layer's "the child inherits none of the host's MCP" contract), and
a boundary whose exception is on by default is not a boundary.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_PRD = "docs/requirements-aare-f/prds/PRD-CORE-281-dispatch-reachability-and-child-trw-access.md"

DISPATCH_ACCESS_ADMISSIONS: dict[str, ConfigAdmission] = {
    "dispatch_tools_exposed": ConfigAdmission(
        field_name="dispatch_tools_exposed",
        owner="PRD-CORE-281-FR01",
        consumer=(
            "trw_mcp.middleware.surface_authority.SurfaceAuthorityMiddleware._resolve -> "
            "trw_mcp.server._surface_manifest_registry.resolve_tool_surface(dispatch_enabled=...)"
        ),
        default_rationale=(
            "false. The dispatch pack is HIGH-RISK (models/config/_defaults.HIGH_RISK_PACKS): its "
            "tools launch another agent process with its own model spend and its own filesystem "
            "reach, so it stays off the advertised surface of a session that never asked for it. "
            "The field exists because the alternatives were both wrong: tool_resolution_mode='all' "
            "exposes EVERY pack to buy one, and a trw_request_tool_access grant is SINGLE-USE, so "
            "the launch-then-poll loop the bundled trw-delegate skill prescribes would need a fresh "
            "20-character justification before every poll."
        ),
        interaction_analysis=(
            "Read once per surface resolution, alongside comms_enabled, and it only ADDS a pack to "
            "the bounded standard resolution -- it cannot narrow one, and it is not consulted at "
            "all under tool_resolution_mode='all'. It is dominated by surface_role='reviewer', "
            "which REPLACES the surface before any mode or pack is read, so it can never widen a "
            "dispatched reviewer (that would be the dispatch-recursion escape REVIEWER_TOOLS "
            "excludes the pack for). Turning it on changes the advertised catalogue, so a client "
            "that listed tools at connect needs the list_changed push the same middleware emits."
        ),
        deprecation_plan=(
            "Retire when a task type legitimately OWNS delegation and STANDARD_TASK_PACKS names the "
            "pack for it; until then removing this field restores 'a shipped skill names two tools "
            "no default session can see'."
        ),
        docs_pointer=_PRD,
        test_pointer="trw-mcp/tests/test_dispatch_surface_reachability.py",
        budget_decision="admitted",
    ),
    "dispatch_child_trw_access": ConfigAdmission(
        field_name="dispatch_child_trw_access",
        owner="PRD-CORE-281-FR02",
        consumer=(
            "trw_mcp.dispatch._resolve.resolve_dispatch_request -> DispatchRequest.with_trw -> "
            "trw_mcp.dispatch._commands.build_command (render_trw_access_argv)"
        ),
        default_rationale=(
            "false. A dispatched child is isolated from the host's config, hooks and MCP servers by "
            "construction (claude --strict-mcp-config with an EMPTY server map, codex "
            "--ignore-user-config), and that isolation is the reason a second opinion is worth "
            "anything. Injecting TRW's own server is the one documented exception, so it is opted "
            "into per call (--with-trw / with_trw=True) or per project by this field -- never "
            "assumed."
        ),
        interaction_analysis=(
            "Supplies only the DEFAULT for DispatchRequest.with_trw; an explicit caller value wins, "
            "exactly like dispatch_default_read_only. It is mutually exclusive with "
            "posture='reviewer' (refused at model construction) because that posture already "
            "injects the server under a bounded role, and it is REFUSED for a client whose registry "
            "entry carries no argv channel rather than degrading to an unwired run. It changes the "
            "argv only: the env allowlist, the read-only posture and the forbidden-token floor are "
            "untouched, so a with_trw child is still write-denied unless --allow-writes was given."
        ),
        deprecation_plan=(
            "Retain while dispatched peers are expected to record their own learnings; removing it "
            "returns dispatch to 'the child has no TRW memory at all', which is the reported defect."
        ),
        docs_pointer=_PRD,
        test_pointer="trw-mcp/tests/test_dispatch_trw_access.py",
        budget_decision="admitted",
    ),
}

__all__ = ["DISPATCH_ACCESS_ADMISSIONS"]
