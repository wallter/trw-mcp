"""Pure client-edge mapping for provider-neutral execution-effort advice.

The adapter returns configuration advice only. It never writes client config
and never claims that the parent harness applied the returned value.
"""

from __future__ import annotations
