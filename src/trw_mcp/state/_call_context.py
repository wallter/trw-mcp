"""Shared TRWCallContext builder (PRD-CORE-141 FR03, DRY'd cycle 23).

Two consumers (``tools/orchestration.py`` and
``tools/_learning_module_helpers.py``) had byte-identical copies of
this helper. PRD-DIST-243 Phase 1 batch 3 consolidates them here so
both files can drop their own copy plus the ``TRWCallContext`` /
``resolve_pin_key`` imports they no longer reference directly.
"""

from __future__ import annotations

from fastmcp import Context

from trw_mcp.state._paths import TRWCallContext, resolve_pin_key

__all__ = ["build_call_context"]


def build_call_context(ctx: Context | None) -> TRWCallContext:
    """Construct a :class:`TRWCallContext` for pin-state helpers
    (PRD-CORE-141 FR03).

    Used by ctx-aware tools so they don't scan-hijack another session's
    on-disk active run via telemetry or PRD knowledge-ID prefetch.
    """
    pin_key = resolve_pin_key(ctx=ctx, explicit=None)
    try:
        raw_session = getattr(ctx, "session_id", None) if ctx is not None else None
    except Exception:
        raw_session = None
    return TRWCallContext(
        session_id=pin_key,
        client_hint=None,
        explicit=False,
        fastmcp_session=raw_session if isinstance(raw_session, str) else None,
    )
