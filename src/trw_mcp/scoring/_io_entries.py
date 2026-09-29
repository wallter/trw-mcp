"""YAML entry read/write helpers for the scoring I/O boundary.

Belongs to the ``_io_boundary.py`` facade. Re-exported there for back-compat
so ``_correlation.py`` / ``_decay.py`` do not import ``FileStateReader`` /
``FileStateWriter`` from the state layer directly (PRD-FIX-061-FR05).
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)
