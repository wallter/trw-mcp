"""TypedDict schemas for the Copilot bootstrap layer.

Extracted from :mod:`trw_mcp.bootstrap._copilot` (PRD-DIST-243 Phase 1
batch 6, cycle 36) to keep that module under the 350-effective-LOC
operator threshold. Holds the 5 TypedDicts that describe Copilot's
hooks.json + path-scoped instruction-file shapes.
"""

from __future__ import annotations

from typing_extensions import TypedDict

__all__ = [
    "CopilotHookCommand",
    "CopilotHookConfig",
    "CopilotHookGroup",
    "CopilotHooksPayload",
    "PathScopedTemplate",
]


class PathScopedTemplate(TypedDict):
    """Template for a path-scoped Copilot instruction file."""

    applyTo: str
    content: str


class CopilotHookCommand(TypedDict):
    """A single command entry inside a Copilot hook group."""

    type: str
    command: str


class CopilotHookGroup(TypedDict):
    """A hook group entry in Copilot hooks.json."""

    description: str
    hooks: list[CopilotHookCommand]


class CopilotHooksPayload(TypedDict):
    """Top-level hooks.json structure for Copilot."""

    version: int
    hooks: dict[str, list[CopilotHookGroup]]


class CopilotHookConfig(TypedDict):
    """Mapping entry for a TRW hook → Copilot event."""

    script: str
    description: str
