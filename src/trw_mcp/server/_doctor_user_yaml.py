"""``user_tier_yaml`` doctor check: personal learnings copied into the project (learning L-5ist).

Before the fix, every ``trw_learn`` also wrote a YAML sidecar and an ``index.yaml`` row under the
PROJECT's ``.trw/learnings/``, including rows routed to the user tier (``user:*``). The installed
gitignore template tracks that folder, so a personal row could be committed to whatever repo the
session ran in. ``trw_learn`` no longer does this; this row finds the copies already on disk.

WARN names how many exist and the fix: the store holds each canonical row, so the files can be
deleted. The row is read-only; TRW never deletes them for you. A copy that was already committed
stays in git history until you rewrite it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server._subcommands_doctor import CheckResult

__all__ = ["check_user_yaml", "user_yaml_row"]

_SHOWN_IDS = 5


def _user_tier_ids(trw_dir: Path) -> set[str]:
    """Ids of every row (any status) in the user namespace of this machine's store."""
    from trw_mcp.state._constants import DEFAULT_LIST_LIMIT
    from trw_mcp.state._store_selection import selected_store
    from trw_mcp.state._tier_routing import USER_NAMESPACE

    store, _project_namespace = selected_store(trw_dir)
    limit = DEFAULT_LIST_LIMIT
    while len(rows := store.list_entries(USER_NAMESPACE, limit=limit)) == limit:  # the store has no offset
        limit *= 2
    return {row.id for row in rows}


def _index_rows(index: Path, user_ids: set[str]) -> int:
    """How many ``index.yaml`` rows carry a user-tier id; 0 when the index is absent or unreadable."""
    from trw_mcp.state.persistence import FileStateReader

    try:
        rows = FileStateReader().read_yaml(index).get("entries", [])
    except Exception:  # justified: the index count is advisory; an unreadable index must not hide the sidecar count
        return 0  # trw-fail-silent-allow: advisory count, the sidecar scan above is the verdict
    if not isinstance(rows, list):
        return 0
    return sum(1 for row in rows if isinstance(row, dict) and str(row.get("id", "")) in user_ids)


def user_yaml_row(target: Path, config: TRWConfig) -> tuple[Literal["PASS", "WARN", "SKIP"], str]:
    """``(status, message)``: WARN when a user-tier row also has a YAML copy in the project's entries dir."""
    from trw_mcp.scoring._yaml_id_index import _build_yaml_path_index
    from trw_mcp.state._store_selection import StoreUnavailableError

    trw_dir = target / ".trw"
    entries_dir = trw_dir / config.learnings_dir / config.entries_dir
    if not entries_dir.is_dir():
        return "PASS", f"{entries_dir} is absent; no project copy of a user-tier learning"
    try:
        user_ids = _user_tier_ids(trw_dir)
    except (StoreUnavailableError, ValueError) as exc:
        return "SKIP", f"user-tier copies not checked: {exc}"
    copies = sorted(lid for lid in _build_yaml_path_index(entries_dir) if lid in user_ids)
    index = trw_dir / config.learnings_dir / "index.yaml"
    indexed = _index_rows(index, user_ids)  # carries the summary too: a row left behind is still a copy
    if not copies and not indexed:
        return "PASS", f"no user-tier learning has a copy under {entries_dir} or a row in {index}"
    shown = f" ({', '.join(copies[:_SHOWN_IDS])}{' ...' if len(copies) > _SHOWN_IDS else ''})" if copies else ""
    return "WARN", (
        f"{len(copies)} user-tier learning(s) have a copy under {entries_dir}{shown} and {indexed} row(s) in "
        f"{index}; that folder is tracked by the installed gitignore, so a commit would publish them. The store "
        "holds each canonical row: delete those files and drop their rows from index.yaml (not done for you). "
        "A copy already committed stays in git history."
    )


def check_user_yaml(target: Path, config: TRWConfig) -> CheckResult:
    """Doctor-registry entry point (imported by name into _subcommands_doctor.py's globals)."""
    from trw_mcp.server._subcommands_doctor import CheckResult

    return CheckResult("user_tier_yaml", *user_yaml_row(target, config))
