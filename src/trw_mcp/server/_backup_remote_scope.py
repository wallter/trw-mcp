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


_LABEL_BATCH = 1000


def labelled_row_count(db_path: Path) -> int | None:
    """Rows in the store labelled above ``team`` (PRD-SEC-023 FR05), a count only; ``None`` when the store cannot be read (refuse).

    The archive is the whole store, so one such row refuses the upload. Each row is judged by the label policy on its namespace, tags and
    stamp, read in batches so a large store is never held in memory at once.
    """
    import json

    from trw_memory._live_stores import connect_registered
    from trw_memory.labels import LabelPolicy, Sink
    from trw_memory.models.entry_factory import new_entry

    policy = LabelPolicy.current()
    # Copied per row (model_construct resolves every default, ~1 ms a row); built by the entry factory, the one sanctioned
    # constructor. A label-only probe, never stored: its id and node id are placeholders.
    template = new_entry(entry_id="-", content="", namespace="default", local_node_id="label-probe")
    labelled = 0
    try:
        conn = connect_registered(db_path, sqlite3, f"{db_path.as_uri()}?mode=ro", uri=True)
        try:
            cursor = conn.execute("SELECT namespace, tags, metadata FROM memories")
            while batch := cursor.fetchmany(_LABEL_BATCH):
                rows = [
                    template.model_copy(
                        update={
                            "namespace": str(ns),
                            "tags": json.loads(tags or "[]"),
                            "metadata": json.loads(meta or "{}"),
                        }
                    )
                    for ns, tags, meta in batch
                ]
                labelled += policy.admit(rows, Sink.PLATFORM).withheld
        finally:
            conn.close()
    except (
        sqlite3.Error,
        ValueError,
        TypeError,
        AttributeError,
    ):  # trw-fail-silent-allow: None means unreadable, which refuses the upload (closed)
        return None
    return labelled


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
