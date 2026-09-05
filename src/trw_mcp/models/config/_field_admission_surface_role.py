"""Admission record for the PRD-SEC-015 reviewer-role selector.

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into :data:`~trw_mcp.models.config._field_admission.FIELD_ADMISSIONS`.
Split out for the same reason every other per-PRD admission table is: the
registry grows once per new public field and would otherwise drift past the
module-size gate each time one is admitted — it sat at 343 of 350 effective LOC
when this entry was written, and a full nine-slot record is ~25 lines.

One field is admitted: ``surface_role``, the session-IDENTITY axis that decides
whether a process is a first-party agent or a bounded read-only reviewer.

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_DOCS = "docs/requirements-aare-f/prds/PRD-SEC-015-reviewer-role-read-only-tool-surface.md"
_TESTS = "trw-mcp/tests/test_reviewer_surface_config.py::test_surface_role_env_override_and_admission"

SURFACE_ROLE_ADMISSIONS: dict[str, ConfigAdmission] = {
    "surface_role": ConfigAdmission(
        field_name="surface_role",
        owner="PRD-SEC-015-FR02",
        consumer="trw_mcp.middleware.surface_authority.SurfaceAuthorityMiddleware._resolve",
        default_rationale=(
            "Defaults to 'agent', which is byte-identical to the pre-PRD behaviour: every existing "
            "session keeps the kernel + task-pack surface it has today. 'reviewer' is never a "
            "default and never inferred — it is declared per PROCESS by the dispatch layer through "
            "TRW_SURFACE_ROLE, because a dispatched reviewer already gets its own stdio server and "
            "process scope is what makes the role unreachable from inside the bounded session."
        ),
        interaction_analysis=(
            "This field DOMINATES every other exposure input. It is resolved before "
            'tool_resolution_mode — including the "all" operator escape, which is deliberately NOT '
            'honoured for a reviewer: "all" widens an operator\'s own session, while this role '
            "contains a subordinate process, so honouring it would let an audited project un-bound "
            "the lane auditing it. It also dominates task-pack resolution and REPLACES (never "
            "subtracts from) the never-hide union, so a future addition to that set cannot "
            "re-widen a reviewer surface. Consequence to weigh before setting it anywhere but in a "
            "dispatch env: writing surface_role: reviewer into a project .trw/config.yaml would "
            "bound every session in that project to the nine read-only tools, including the "
            "operator's own; the intended selection is per-process env only, and an env-declared "
            "reviewer can never be downgraded by a config file (FR14)."
        ),
        deprecation_plan=(
            "Retain. Removing it restores the measured defect: a read-only dispatched reviewer "
            "holding trw_deliver, trw_build_check and trw_learn (24 + 12 + 12 calls across 11 runs, "
            "2026-08-27) with no session identity."
        ),
        docs_pointer=_DOCS,
        test_pointer=_TESTS,
        budget_decision="admitted",
    ),
}

__all__ = ["SURFACE_ROLE_ADMISSIONS"]
