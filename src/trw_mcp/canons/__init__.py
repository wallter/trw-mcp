"""Typed standard-library canon registry + live-process fingerprint.

PRD-INFRA-164. The ``registry`` module is the strict, dependency-free authority
that turns ``data/framework_canons.json`` into typed source/mirror/install/
runtime/version-surface views. The ``fingerprint`` module freezes a process's
loaded versions and realized public MCP surface into a stable digest.

Consumers should import the public surface from ``trw_mcp.canons.registry`` and
``trw_mcp.canons.fingerprint``. This package ``__init__`` intentionally re-exports
nothing heavy so importing ``trw_mcp.canons`` stays cheap and dependency free.
"""

from __future__ import annotations

__all__: list[str] = []
