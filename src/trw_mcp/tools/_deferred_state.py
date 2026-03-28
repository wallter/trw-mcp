"""Shared state for deferred delivery -- thread handle and lock.

Extracted to break the circular import between ceremony.py and
_deferred_delivery.py. Both modules import from here instead of
from each other.
"""

from __future__ import annotations

import threading

# Deferred delivery thread handle and lock.
# Previously lived in ceremony.py; extracted here so
# _deferred_delivery.py can import without a cycle.
_deferred_thread: threading.Thread | None = None
_deferred_lock = threading.Lock()
