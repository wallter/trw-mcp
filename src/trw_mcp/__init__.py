"""TRW Framework MCP Server — orchestration, requirements, and self-learning tools.

SQLite Driver Selection
-----------------------
The very first import below performs an idempotent swap of stdlib ``sqlite3``
with ``pysqlite3-binary`` (when installed) so that every subsequent
``import sqlite3`` resolves to a SQLite build that carries the WAL-reset bug
fix. ``trw_memory`` does the same swap; importing either package first is
sufficient. The swap is a no-op when ``pysqlite3`` is absent.

Error Handling Policy (PRD-FIX-043)
------------------------------------
Every ``except Exception`` block in this package MUST be annotated with a  # justified: fail-open
``# justified: <category>, <rationale>`` comment. Recognised categories:

- **fail-open** — telemetry, logging, or analytics that must never block
  the primary tool flow. Swallowing is acceptable; logging at ``debug``
  or ``warning`` with ``exc_info=True`` is preferred.
- **boundary** — external system calls (Anthropic API, subprocess, network)
  where the full exception surface is unpredictable. Always log with
  ``exc_info=True`` at ``warning`` level.
- **cleanup** — resource release (file locks, temp files, connections)
  where failure during cleanup must not mask the original result.
- **import-guard** — optional dependency checks (``try: import X``).
  Log at ``warning`` with install instructions.
- **scan-resilience** — iterating over user-generated data (YAML entries,
  JSONL lines) where a single malformed record must not abort the scan.
  Log at ``debug`` per-item, summarise at ``warning`` if any skipped.

Bare ``except Exception: pass`` without logging is prohibited.  # justified: fail-open
New ``except Exception`` blocks require both the ``# justified:`` comment
and a corresponding log call.
"""

# MUST be first and INLINE for the same reason as trw_memory/__init__.py:
# any import that ends up touching ``trw_memory.storage`` will eagerly
# load ``sqlite_backend`` → ``_init_helpers`` → ``_connection``, each of
# which captures stdlib ``sqlite3`` into its module namespace. By doing
# the swap inline here with no other package imports, we guarantee that
# all subsequent submodule loads see the swapped ``sys.modules["sqlite3"]``.
# The swap is idempotent — if trw_memory loaded first it has already run.
try:
    import sys as _sys
    import pysqlite3 as _pysqlite3  # noqa: F401

    _sys.modules["sqlite3"] = _pysqlite3
    _sys.modules["sqlite3.dbapi2"] = _pysqlite3.dbapi2
    _pysqlite3._trw_pysqlite3_active = True  # type: ignore[attr-defined]
except ImportError:
    pass

# Expose the observability shim for callers that want to inspect which
# driver is active. ``trw_memory`` re-runs the swap idempotently if it
# happens to load first.
from trw_memory.storage import _dbapi as _dbapi  # noqa: F401, I001, E402

from importlib.metadata import version as _pkg_version  # noqa: E402

__version__: str = _pkg_version("trw-mcp")

__all__ = ["__version__"]
