"""Cursor IDE + cursor-cli distill channels (PRD-DIST-2401).

Re-exports the public emitter classes consumed by bootstrap and the
trw_channel_render MCP tool.
"""

from __future__ import annotations

from trw_mcp.channels.cursor._agents_md_segment import (
    AgentsMdSegmentWriter as AgentsMdSegmentWriter,
)
from trw_mcp.channels.cursor._mdc_emitter import (
    MdcEmitter as MdcEmitter,
)
from trw_mcp.channels.cursor._mdc_emitter import (
    MdcEmitterError as MdcEmitterError,
)

__all__ = [
    "AgentsMdSegmentWriter",
    "MdcEmitter",
    "MdcEmitterError",
]
