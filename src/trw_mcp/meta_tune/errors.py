"""Typed SAFE-001 error classes.

This module is imported on the MCP server startup path via
``meta_tune.boot_checks``.  Keep it independent from ``trw_mcp.telemetry``'s
package initializer: importing the telemetry package eagerly pulls in the
client/state/model/scoring stack and can re-enter ``state._paths`` while it is
still partially initialized.
"""

from __future__ import annotations


class MetaTuneSafetyUnavailableError(RuntimeError):
    """Raised when a required SAFE-001 dependency is unavailable."""

    def __init__(self, *, dependency_id: str, activation_gate_blocked_reason: str) -> None:
        self.dependency_id = dependency_id
        self.activation_gate_blocked_reason = activation_gate_blocked_reason
        super().__init__(f"{dependency_id}: {activation_gate_blocked_reason}")


class MetaTuneBootValidationError(RuntimeError):
    """Raised when SAFE-001 boot-time defaults do not resolve to reality."""


class KillSwitchNotFoundError(FileNotFoundError):
    """Raised when the anchored kill-switch path cannot be resolved."""
