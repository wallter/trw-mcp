"""Custom exception hierarchy for TRW MCP server.

All exceptions include structured context for logging and debugging.
Never use bare ``except:`` — always catch specific types from this module.
"""

from __future__ import annotations

# PRD-CORE-001: Base MCP tool suite — error hierarchy


__all__ = ["ConfigError", "ReflectionError", "StateError", "TRWError", "ValidationError"]


class TRWError(Exception):
    """Base exception for all TRW MCP server errors.

    Args:
        message: Human-readable error description.
        suggestion: Optional remediation hint for tool error handlers.
        context: Structured key-value context for logging.
    """

    def __init__(
        self,
        message: str,
        *,
        suggestion: str = "",
        **context: str | int | float | bool | None,
    ) -> None:
        super().__init__(message)
        self.suggestion = suggestion
        self.context: dict[str, str | int | float | bool | None] = context


class StateError(TRWError):
    """Error reading or writing framework state.

    Raised when run.yaml, events.jsonl, or other state files
    cannot be read, written, or parsed.
    """


class ValidationError(TRWError):
    """Schema or contract validation failure.

    Raised when output contracts, phase exit criteria,
    or PRD quality gates are not met.
    """


class ReflectionError(TRWError):
    """Error during reflection/learning cycle.

    Raised when event analysis, learning extraction,
    or pattern discovery fails.
    """


class ConfigError(TRWError):
    """Invalid or missing configuration.

    Raised when .trw/config.yaml is malformed or
    required configuration values are missing.
    """
