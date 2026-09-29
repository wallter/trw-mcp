"""PRD-CORE-149-FR10: rendering sub-modules extracted from ``_renderer.py``.

Public API lives on the ``ProtocolRenderer`` class in ``_renderer.py``; this
sub-package holds the Antigravity instruction renderer. The per-model-family
opencode renderers were deleted by PRD-CORE-301-FR02: opencode now renders the
shared claude-code block (``sections._tool_lifecycle.render_opencode_instructions``).
"""

from __future__ import annotations
