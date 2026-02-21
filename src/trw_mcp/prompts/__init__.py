"""TRW MCP prompts — AARE-F requirements engineering prompts and centralized messaging.

Submodules:
- ``aaref``: AARE-F requirements engineering prompts registered as MCP prompts (PRD-CORE-001).
- ``messaging``: Centralized AI-facing message registry, value-oriented framing (PRD-INFRA-012).
"""

from trw_mcp.prompts.messaging import get_message, get_message_or_default

__all__ = ["get_message", "get_message_or_default"]
