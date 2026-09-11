"""CLAUDE.md ceremony-table data — the tool list and its truncation caps.

Consumed by ``_renderer.py`` to build the behavioral-protocol table.

PRD-CORE-093 removed learning promotion into CLAUDE.md: ``trw_session_start``
already delivers task-relevant learnings through focused hybrid recall, so
re-rendering them into the instruction file cost ~1,800 tokens per message and
— because ``trw_deliver`` re-synced after every delivery — rotated the section
and invalidated the prompt cache. The five ``render_*`` functions that built it
were deprecated in 0.37.0 and are now deleted; nothing in ``src/`` called them.
"""

from __future__ import annotations

from typing import NamedTuple

# Named caps for list truncation (not user-tunable)
CLAUDEMD_LEARNING_CAP = 10
CLAUDEMD_PATTERN_CAP = 5
BEHAVIORAL_PROTOCOL_CAP = 12


class CeremonyTool(NamedTuple):
    """A lifecycle-critical MCP tool with usage guidance."""

    phase: str
    tool: str
    when: str
    what: str
    example: str


# Phase descriptions for the 6-phase execution model
PHASE_DESCRIPTIONS: list[tuple[str, str]] = [
    ("RESEARCH", "Discover context, audit codebase, register findings"),
    ("PLAN", "Design implementation approach, identify dependencies"),
    ("IMPLEMENT", "Execute work with periodic checkpoints, shard self-review before completing"),
    ("VALIDATE", "Run trw_build_check, verify coverage, lead checks shard integration"),
    ("REVIEW", "Review diff for quality (DRY/KISS/SOLID), fix gaps, record learnings"),
    ("DELIVER", "Sync artifacts, checkpoint, close run"),
]

# 11 lifecycle-critical tools in execution order
# Each "what" field uses value framing (WHY it matters) not mechanical description (WHAT it does)
CEREMONY_TOOLS: list[CeremonyTool] = [
    CeremonyTool(
        "Start",
        "trw_session_start",
        "First action \u2014 loads prior learnings + recovers active run state",
        "Start from accumulated knowledge instead of zero \u2014 prior agents already found gotchas for your area",
        "trw_session_start(query='task domain')",
    ),
    CeremonyTool(
        "Start",
        "trw_recall",
        "Quick tasks \u2014 retrieves relevant prior learnings without a full run",
        "Surface discoveries from past sessions so you don't repeat solved problems",
        "trw_recall('auth patterns', min_impact=0.7)",
    ),
    CeremonyTool(
        "Start",
        "trw_status",
        "When resuming \u2014 shows current phase, progress, and next steps",
        "Pick up where you left off instead of redoing completed work",
        "trw_status()",
    ),
    CeremonyTool(
        "RESEARCH",
        "trw_init",
        "New structured tasks \u2014 creates run directory for tracking",
        "Enables run checkpoints; a durable native handoff can preserve work without creating a run",
        "trw_init(task_name='...')",
    ),
    CeremonyTool(
        "Any",
        "trw_learn",
        "On errors, discoveries, or gotchas",
        "Saves your finding so no future agent repeats your mistake \u2014 this is how institutional knowledge grows",
        "trw_learn(summary='...', impact=0.8)",
    ),
    CeremonyTool(
        "Any",
        "trw_checkpoint",
        "After milestones \u2014 preserves progress across context compactions",
        "Preserves material unfinished work; a durable native handoff with a next-read pointer is also valid",
        "trw_checkpoint(message='...')",
    ),
    CeremonyTool(
        "PLAN",
        "trw_prd_create",
        "When defining requirements for a new feature or fix",
        "Ambiguous requirements are the cheapest defect to fix in spec and the most expensive in code",
        "trw_prd_create(input_text='...')",
    ),
    CeremonyTool(
        "PLAN",
        "trw_prd_validate",
        "Before implementation begins",
        "Catches requirement gaps before they become code bugs \u2014 cheaper to fix in spec than in code",
        "trw_prd_validate(prd_path='...')",
    ),
    CeremonyTool(
        "VALIDATE",
        "trw_build_check",
        "After implementation and before delivery",
        "Catches failures before delivery \u2014 a failure found after delivery cascades into multi-file rework",
        "trw_build_check(tests_passed=<bool>, test_count=<n>, failure_count=<n>, static_checks_clean=<bool|null>, scope='<exact command>')",
    ),
    CeremonyTool(
        "REVIEW",
        "review diff",
        "After VALIDATE \u2014 check quality (DRY/KISS/SOLID), fix gaps, record learnings",
        "Independent review catches what self-review misses \u2014 implementers optimize for completion, reviewers for correctness",
        "Read diff, fix gaps, trw_learn(summary='...')",
    ),
    CeremonyTool(
        "DELIVER",
        "trw_instructions_sync",
        "At delivery",
        "Refreshes the client's instruction file (CLAUDE.md / AGENTS.md / etc.) so every future session starts with your best insights",
        "trw_instructions_sync()",
    ),
    CeremonyTool(
        "DELIVER",
        "trw_deliver",
        "For completed-work acceptance under the delivery gates",
        "Recorded learnings already persist; unfinished work does not require delivery",
        "trw_deliver()",
    ),
]
