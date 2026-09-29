"""Recall-tracking JSONL tail-reader for the scoring I/O boundary.

Belongs to the ``_io_boundary.py`` facade. Re-exported there for back-compat
(PRD-FIX-061-FR05) so ``correlate_recalls`` reads the tail of
``recall_tracking.jsonl`` without importing ``FileStateReader`` from the
state layer.

``_read_recall_tracking_jsonl`` looks ``_tail_lines`` up through the facade
module so tests that monkeypatch ``trw_mcp.scoring._io_boundary._tail_lines``
still intercept the call.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)
