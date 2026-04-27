"""Typed SAFE-001 error classes."""

from __future__ import annotations

from trw_mcp.telemetry.event_base import DefaultResolutionError


class MetaTuneSafetyUnavailableError(RuntimeError):
    """Raised when a required SAFE-001 dependency is unavailable."""

    def __init__(self, *, dependency_id: str, activation_gate_blocked_reason: str) -> None:
        self.dependency_id = dependency_id
        self.activation_gate_blocked_reason = activation_gate_blocked_reason
        super().__init__(f"{dependency_id}: {activation_gate_blocked_reason}")


class MetaTuneBootValidationError(DefaultResolutionError):
    """Raised when SAFE-001 boot-time defaults do not resolve to reality."""


class KillSwitchNotFoundError(FileNotFoundError):
    """Raised when the anchored kill-switch path cannot be resolved."""
