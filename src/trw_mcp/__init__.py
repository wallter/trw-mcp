"""TRW Framework MCP Server — orchestration, requirements, and self-learning tools.

SQLite Driver Selection
-----------------------
There is exactly ONE place that decides which SQLite module this process runs:
``trw_memory.storage._dbapi``, which ranks the stdlib engine against an
installed ``pysqlite3`` on ``(carries the WAL-reset fix, version)`` and never
lets an older wheel replace a newer interpreter. The first import below reaches
it, before anything in this package can ``import sqlite3``.

This module used to perform its own inline ``sys.modules`` swap as well, on the
reasoning that ``trw-mcp`` might be installed without ``trw-memory``. It cannot
be — ``trw-memory`` is a required dependency — and a second, policy-free
decision point could only ever disagree with the first (PRD-INFRA-185 FR05).

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

# The SQLite driver policy lives in trw_memory.storage._dbapi, and this import
# MUST stay first: importing ``trw_memory.storage`` runs the selection before
# ``_wal_checkpoint``/``_connection``/``sqlite_backend`` capture ``sqlite3`` in
# their own namespaces, and before any trw_mcp submodule loads. The binding is
# also the observability shim — callers ask ``_dbapi.backend()`` /
# ``_dbapi.sqlite_version()`` which engine won.
from trw_memory.storage import _dbapi as _dbapi  # noqa: I001

import importlib.metadata as _importlib_metadata
from datetime import datetime as _datetime
from datetime import timezone as _timezone
import re as _re
from pathlib import Path as _Path


#: When this process imported trw_mcp — for a server, its boot. Read by the
#: unpinned deliver gate as "every unpinned change since this server started";
#: it must NOT live in the gate module, which is imported lazily at the first
#: deliver call, after the edits it needs to see (release-verify 2026-09-17 F1).
PROCESS_STARTED_AT = _datetime.now(_timezone.utc)


def _resolve_version() -> str:
    """Resolve the package version from source first, then metadata.

    Development checkouts can be imported via ``PYTHONPATH`` while an older
    ``trw-mcp`` wheel/editable install is still present in the environment. In
    that case ``importlib.metadata.version("trw-mcp")`` reports the installed
    distribution instead of the source tree under test, making CLI/status
    surfaces advertise a stale version. Prefer the adjacent ``pyproject.toml``
    when it exists; wheels fall back to distribution metadata because the
    source ``pyproject.toml`` is not shipped.
    """
    pyproject = _Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.exists():
        match = _re.search(r'^version = "([^"\n]+)"', pyproject.read_text(encoding="utf-8"), _re.MULTILINE)
        if match is not None:
            return match.group(1)
    try:
        return _importlib_metadata.version("trw-mcp")
    except _importlib_metadata.PackageNotFoundError:  # pragma: no cover - defensive source-tree fallback
        return "0.0.0"


__version__: str = _resolve_version()

__all__ = ["__version__"]
