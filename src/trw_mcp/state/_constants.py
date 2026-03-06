"""Shared constants for state modules.

Centralizes magic numbers and string prefixes that were previously
hardcoded across multiple state modules.
"""

from __future__ import annotations

# Effective "unlimited" cap for list/search operations across the memory adapter,
# knowledge topology, and report modules.
DEFAULT_LIST_LIMIT: int = 10_000

# Default namespace for memory backend operations.
DEFAULT_NAMESPACE: str = "default"
