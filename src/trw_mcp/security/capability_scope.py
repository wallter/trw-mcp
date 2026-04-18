"""Per-tool capability scoping and rate-limiting filter (PRD-INFRA-SEC-001 FR-2).

``CapabilityScope`` declares, for a single tool:

* ``tool_name`` — the tool this scope governs.
* ``allowed_args`` — mapping of arg-name → allowed value. ``None`` means the
  scope does not constrain argument values (any args accepted). An **empty
  dict** means *no args permitted* (strict mode).
* ``rate_limit_per_min`` — optional cap on calls-per-60-seconds. ``None``
  means unlimited.

``CapabilityFilter`` wraps an MCP adapter-like callable (``call(name, args)``)
and enforces the registered scopes before delegating. Violations raise
``CapabilityScopeError``.

Default scopes for known tool families can be supplied via
``default_scopes_for_family``; callers may pass a dict keyed on tool prefix
(e.g. ``"trw_"``) to obtain a starter scope when no explicit entry exists.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


class CapabilityScopeError(Exception):
    """Raised when a tool call violates its declared ``CapabilityScope``."""


class CapabilityScope(BaseModel):
    """Authorized invocation envelope for a single MCP tool."""

    tool_name: str = Field(..., description="Name of the tool this scope guards.")
    allowed_args: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Mapping of allowed arg-name → expected value. None = unconstrained. "
            "Empty dict = strict (no args permitted)."
        ),
    )
    rate_limit_per_min: int | None = Field(
        default=None,
        ge=0,
        description="Max calls per rolling 60s window. None = unlimited.",
    )


def _check_args(call_args: Mapping[str, Any], scope: CapabilityScope) -> None:
    if scope.allowed_args is None:
        return
    for arg_name in call_args:
        if arg_name not in scope.allowed_args:
            raise CapabilityScopeError(
                f"tool {scope.tool_name!r} received disallowed arg {arg_name!r}"
            )


def apply_scope(tool_call: dict[str, Any], scope: CapabilityScope) -> dict[str, Any]:
    """Validate ``tool_call`` against ``scope`` or raise ``CapabilityScopeError``.

    ``tool_call`` must contain keys ``name`` (str) and ``args`` (dict). Returns
    the same dict unchanged on success so callers can chain.
    """

    name = tool_call.get("name")
    if name != scope.tool_name:
        raise CapabilityScopeError(
            f"tool_call name {name!r} does not match scope {scope.tool_name!r}"
        )
    args = tool_call.get("args", {}) or {}
    if not isinstance(args, dict):
        raise CapabilityScopeError("tool_call 'args' must be a dict")
    _check_args(args, scope)
    logger.debug(
        "capability_scope_apply",
        tool=scope.tool_name,
        outcome="accepted",
    )
    return tool_call


AdapterCall = Callable[[str, dict[str, Any]], Any]


class CapabilityFilter:
    """Adapter wrapper that enforces per-tool capability scopes.

    ``adapter_call`` is any callable matching ``(tool_name, args) -> result``.
    ``scopes`` maps tool-name → ``CapabilityScope``. Calls to tools missing
    from ``scopes`` raise ``CapabilityScopeError`` (deny-by-default; FR-2).
    """

    def __init__(
        self,
        adapter_call: AdapterCall,
        scopes: Mapping[str, CapabilityScope],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._adapter_call = adapter_call
        self._scopes: dict[str, CapabilityScope] = dict(scopes)
        self._clock = clock
        self._call_times: dict[str, deque[float]] = {}

    def _check_rate(self, scope: CapabilityScope) -> None:
        limit = scope.rate_limit_per_min
        if limit is None:
            return
        now = self._clock()
        window_start = now - 60.0
        bucket = self._call_times.setdefault(scope.tool_name, deque())
        while bucket and bucket[0] < window_start:
            bucket.popleft()
        if len(bucket) >= limit:
            raise CapabilityScopeError(
                f"tool {scope.tool_name!r} exceeded rate_limit_per_min={limit}"
            )
        bucket.append(now)

    def call(self, tool_name: str, args: dict[str, Any]) -> Any:
        scope = self._scopes.get(tool_name)
        if scope is None:
            logger.warning(
                "capability_scope_unknown_tool",
                tool=tool_name,
                outcome="rejected",
            )
            raise CapabilityScopeError(f"tool {tool_name!r} has no registered scope")
        apply_scope({"name": tool_name, "args": args}, scope)
        self._check_rate(scope)
        logger.info(
            "capability_scope_call",
            tool=tool_name,
            outcome="forwarded",
        )
        return self._adapter_call(tool_name, args)


def default_scopes_for_family(
    family_prefix: str,
    tool_names: list[str],
    *,
    rate_limit_per_min: int | None = None,
) -> dict[str, CapabilityScope]:
    """Build permissive default scopes for a known tool family.

    Useful for config-driven bootstrap where a tool family (e.g. ``trw_``) is
    granted a uniform baseline scope. The resulting scopes allow any
    arguments (``allowed_args=None``) and share the supplied rate limit.
    """

    return {
        name: CapabilityScope(
            tool_name=name,
            allowed_args=None,
            rate_limit_per_min=rate_limit_per_min,
        )
        for name in tool_names
        if name.startswith(family_prefix)
    }
