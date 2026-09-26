"""The post-cut MCP tool surface, stated once (PRD-CORE-300-FR01, slice S0).

PRD-CORE-300 cuts the registered surface from 51 tools to 15 across slices
S1 to S12. Each slice reads its target from here rather than restating it, so
no slice before S11b re-derives the kernel or bumps ``KERNEL_VERSION``; S11b
makes ``surface_packs.KERNEL_TOOLS`` equal :data:`POST_CUT_KERNEL` and re-pins
the digest once.

Pure: stdlib types only, no ``trw_mcp`` imports, like ``surface_packs``.
"""

from __future__ import annotations

#: Always registered after the cut. ``trw_code`` has been registered since S10,
#: which landed after the code index was bounded (FR15).
POST_CUT_KERNEL: tuple[str, ...] = (
    "trw_session_start",
    "trw_init",
    "trw_status",
    "trw_recall",
    "trw_learn",
    "trw_checkpoint",
    "trw_deliver",
    "trw_build_check",
    "trw_review",
    "trw_prd_validate",
    "trw_code",
)

#: Registered only while the named config flag is true (its default in parentheses):
#: assess_enabled (false), comms_enabled (true), dispatch_tools_exposed (false).
POST_CUT_FLAGGED: dict[str, str] = {
    "trw_assess": "assess_enabled",
    "trw_send": "comms_enabled",
    "trw_inbox": "comms_enabled",
    "trw_dispatch": "dispatch_tools_exposed",
}

#: Every tool that exists after the cut: 15 distinct, 13 registered by default.
POST_CUT_SURFACE: frozenset[str] = frozenset({*POST_CUT_KERNEL, *POST_CUT_FLAGGED})

#: Tools the cut adds: none of them was in the pre-cut registry of 51.
POST_CUT_NEW_TOOLS: frozenset[str] = frozenset({"trw_code"})

#: The reviewer bound after S11b (NFR02); the per-slice steps are in the PRD.
POST_CUT_REVIEWER_TOOLS: frozenset[str] = frozenset({"trw_recall", "trw_code"})

__all__ = [
    "POST_CUT_FLAGGED",
    "POST_CUT_KERNEL",
    "POST_CUT_NEW_TOOLS",
    "POST_CUT_REVIEWER_TOOLS",
    "POST_CUT_SURFACE",
]
