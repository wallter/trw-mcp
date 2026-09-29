"""Setting-source argv for dispatched claude children (PUBLIC, BSL-1.1).

Belongs to the ``trw_mcp.dispatch`` package; consumed by the claude entry in
:mod:`._client_specs`.
"""

from __future__ import annotations

import os

#: Claude children load user AND project setting sources: with ``user`` alone the
#: child never sees the project's AGENTS.md/CLAUDE.md (measured 2026-09-26 on
#: claude 2.1.283: "NONE" vs the heading). ``disableAllHooks`` keeps the original
#: isolation intent -- the project's hooks (its "ceremony") do not run in the child.
#: Opt out with TRW_DISPATCH_CLAUDE_SETTING_SOURCES=user.
_CLAUDE_SETTING_SOURCES = os.environ.get("TRW_DISPATCH_CLAUDE_SETTING_SOURCES", "user,project").strip() or "user"
if not set(_CLAUDE_SETTING_SOURCES.split(",")) <= {"user", "project", "local"}:  # pragma: no cover - env guard
    raise ValueError(f"TRW_DISPATCH_CLAUDE_SETTING_SOURCES={_CLAUDE_SETTING_SOURCES!r}: use user[,project][,local]")
CLAUDE_CONTEXT_ARGV: tuple[str, ...] = (
    "--setting-sources",
    _CLAUDE_SETTING_SOURCES,
    "--settings",
    '{"disableAllHooks":true}',
)
