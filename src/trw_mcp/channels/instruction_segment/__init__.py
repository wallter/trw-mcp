"""Generic instruction-segment renderer (Phase D1 — PRD-DIST-2400 §3).

Re-exports the public API for downstream clients (Codex, opencode,
Antigravity-CLI, Copilot).
"""

from __future__ import annotations

from trw_mcp.channels.instruction_segment._renderer import (
    InstructionSegmentResult,
    render_instruction_segment,
)

__all__ = [
    "InstructionSegmentResult",
    "render_instruction_segment",
]
