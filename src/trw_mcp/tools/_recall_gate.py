"""Import path kept for ``tools/_recall_impl.py``; the predicate lives in ``state/_recall_gate.py``.

Delete this module once ``_recall_impl.py`` imports from ``trw_mcp.state._recall_gate``
(that file is owned by another lane in the 6.0.0 formation).
"""

from trw_mcp.state._recall_gate import RecallPath as RecallPath
from trw_mcp.state._recall_gate import learnings_injection_allowed as learnings_injection_allowed
