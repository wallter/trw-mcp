"""Which stores `backup create` may upload (MEMORY-LEAK-REMOTE-BACKUP-WHOLE-STORE).

The backup archive is a copy of the WHOLE served store file: every project's rows and the ``user:*`` tier that
PRD-CORE-280 says never leaves the machine. The remote leg may therefore carry only a store that holds nothing but the
invoking project's own namespace; any other namespace, and always any ``user:*`` row, refuses the upload (fail closed).
The local archive is unaffected. A per-namespace filtered export is the real feature (BACKLOG
REMOTE-BACKUP-FILTERED-EXPORT); refusing a default-off feature beats leaking.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_USER_PREFIX = "user:"


def invoking_namespace() -> str:
    """The invoking project's ``project_namespace``; ``""`` when it is unpinned or cannot be read (nothing is allowed then)."""
    from trw_mcp.server._subcommands_backup import _invoking_trw_dir
    from trw_mcp.state._namespace_pin_read import pinned_namespace

    trw_dir = _invoking_trw_dir()
    if trw_dir is None:
        return ""
    try:
        return pinned_namespace(trw_dir)
    except Exception:  # trw-fail-silent-allow: "" means no namespace is allowed, which refuses the upload (closed)
        return ""


def namespace_census(db_path: Path) -> dict[str, int] | None:
    """Rows per namespace in the store, counts only; ``None`` when the store cannot be read (treated as refuse)."""
    from trw_memory._live_stores import connect_registered

    try:
        conn = connect_registered(db_path, sqlite3, f"{db_path.as_uri()}?mode=ro", uri=True)
        try:
            return {
                str(ns): int(n) for ns, n in conn.execute("SELECT namespace, count(*) FROM memories GROUP BY namespace")
            }
        finally:
            conn.close()
    except sqlite3.Error:  # trw-fail-silent-allow: None means unreadable, which refuses the upload (closed)
        return None


def remote_upload_refusal(db_path: Path) -> str | None:
    """Why the store at *db_path* must not be uploaded, or ``None`` when it holds only the invoking project's rows."""
    census = namespace_census(db_path)
    if census is None:
        return "the store's namespaces could not be read"
    allowed = invoking_namespace()
    foreign = {ns: n for ns, n in sorted(census.items()) if ns != allowed or ns.startswith(_USER_PREFIX)}
    if not foreign:
        return None
    listing = ", ".join(f"{ns} ({n} row{'s' if n != 1 else ''})" for ns, n in foreign.items())
    return f"the store also holds other namespaces than this project's: {listing}"
