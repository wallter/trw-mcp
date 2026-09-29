"""Output contract validation for shard and wave artifacts.

Provides a protocol-based contract validator and a file-system implementation
that checks declared output files exist and contain required schema keys.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)
