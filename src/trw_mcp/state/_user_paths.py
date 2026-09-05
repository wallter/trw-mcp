"""Machine-local (user-space) memory path resolution -- PRD-CORE-185 FR01.

The implementation moved to :mod:`trw_memory.user_paths` under PRD-CORE-253
FR01: the loopback memory daemon has to resolve the same directory, and
trw-memory cannot import trw-mcp (the dependency runs the other way). FR01
requires that the promotion leave **one** resolver rather than two that can
drift, so this module is now the trw-mcp import site for it and nothing else.

Resolution precedence is unchanged (``TRW_USER_DIR`` > ``$XDG_DATA_HOME`` >
``~/.trw``); see the promoted module for the full contract. Existing call sites
and their ``monkeypatch.setattr("trw_mcp.state._user_paths.resolve_user_memory_dir", ...)``
seams keep working, because this is still the module attribute they bind to.
"""

from __future__ import annotations

from trw_memory.user_paths import USER_MEMORY_SUBDIR as USER_MEMORY_SUBDIR
from trw_memory.user_paths import resolve_user_memory_dir as resolve_user_memory_dir

__all__ = ["USER_MEMORY_SUBDIR", "resolve_user_memory_dir"]
