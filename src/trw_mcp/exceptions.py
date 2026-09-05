"""Custom exception hierarchy for TRW MCP server.

All exceptions include structured context for logging and debugging.
Never use bare ``except:`` — always catch specific types from this module.
"""

from __future__ import annotations

# PRD-CORE-001: Base MCP tool suite — error hierarchy


__all__ = [
    "AgentFormatError",
    "ConfigError",
    "NamespaceEnumerationError",
    "ReflectionError",
    "StateError",
    "TRWError",
    "ValidationError",
]


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


class AgentFormatError(TRWError):
    """A bundled agent cannot be materialized for the requested client.

    Raised when a client has no agent surface, when a client id is outside the
    per-client format registry, or when a bundled agent's frontmatter carries a
    key no client entry has decided the fate of. Deliberately typed: returning a
    claude-code-shaped string for a harness that cannot parse it is the defect
    PRD-CORE-252 exists to remove, so callers must handle the failure rather
    than receive a plausible-looking wrong answer.
    """


class NamespaceEnumerationError(StateError):
    """A store could not list its namespaces, so "absent" is not knowable.

    Raised by :func:`trw_mcp.state._backend_id_lookup.resolve_entry_in_backend`
    when the caller did not name a namespace and the backend could not enumerate
    them. The unnamed namespace is still probed first — one definite attempt is
    worth making — but a MISS after a failed enumeration is not evidence of
    absence: the row may sit in any of the namespaces that were never listed.
    Reporting it as ``not_found`` turned a broken store into a confident denial.
    """
