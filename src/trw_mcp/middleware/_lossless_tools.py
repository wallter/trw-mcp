"""Exact bounded protocols excluded from lossy response presentation.

Comms producers own their byte/item bounds. Repeating an unacknowledged page
must preserve the original envelope, timestamps and explicit empty fields.
This set changes presentation only, never dispatch or authorization.
"""

from typing import Final

LOSSLESS_COMMS_TOOLS: Final[frozenset[str]] = frozenset({"trw_send", "trw_inbox"})
