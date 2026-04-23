"""PRD-CORE-149-FR10: rendering sub-modules extracted from ``_renderer.py``.

Public API lives on the ``ProtocolRenderer`` class in ``_renderer.py``; this
sub-package holds supporting helpers and per-family opencode renderers.
"""

from __future__ import annotations

from trw_mcp.state.claude_md.renderers._review_and_opencode import (
    render_gemini_instructions as render_gemini_instructions,
)
from trw_mcp.state.claude_md.renderers._review_and_opencode import (
    render_opencode_claude as render_opencode_claude,
)
from trw_mcp.state.claude_md.renderers._review_and_opencode import (
    render_opencode_generic as render_opencode_generic,
)
from trw_mcp.state.claude_md.renderers._review_and_opencode import (
    render_opencode_gpt as render_opencode_gpt,
)
from trw_mcp.state.claude_md.renderers._review_and_opencode import (
    render_opencode_qwen as render_opencode_qwen,
)

__all__ = [
    "render_gemini_instructions",
    "render_opencode_claude",
    "render_opencode_generic",
    "render_opencode_gpt",
    "render_opencode_qwen",
]
