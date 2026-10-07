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

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import structlog

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server._subcommands_doctor import CheckResult

__all__ = ["check_user_yaml", "user_yaml_row"]

logger = structlog.get_logger(__name__)

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


def _git(target: Path, *argv: str) -> subprocess.CompletedProcess[bytes] | None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        return subprocess.run(  # noqa: S603 -- fixed argv, no shell
            ["git", "-C", str(target), *argv],  # noqa: S607
            capture_output=True,
            env=env,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("user_yaml_git_unavailable", reason=type(exc).__name__)
        return None


def _git_keeps_out_of_commits(target: Path, paths: list[Path]) -> bool:
    """True only when git says EVERY path is ignored and none is already tracked; any doubt is False.

    Not a git repo, no git, or a timeout all answer False, so the caller keeps its cautious wording. One
    ``check-ignore`` per path: its exit code is 0 when ANY path is ignored. A file tracked before the ignore rule
    stays tracked, so ``ls-files`` is asked too.
    """
    if not paths:
        return False
    for path in paths:
        ignored = _git(target, "check-ignore", "-q", "--", str(path))
        if ignored is None or ignored.returncode != 0:
            return False
        tracked = _git(target, "ls-files", "--error-unmatch", "--", str(path))
        if tracked is None or tracked.returncode == 0:
            return False  # tracked (0) or unanswerable
    return True


def _copies_by_id(entries_dir: Path, user_ids: set[str]) -> tuple[dict[str, list[Path]], list[Path]]:
    """Every YAML file under *entries_dir* whose ``id`` is a user-tier id (duplicates of one id are all kept),
    and the files whose id could not be identified at all.

    The id comes from the line scan the learning index uses, so a sidecar with malformed YAML after its ``id:``
    line is still identified: git would publish it just the same.
    """
    from trw_mcp.scoring._yaml_id_index import _read_learning_id
    from trw_mcp.state._helpers import iter_yaml_entry_files
    from trw_mcp.state.persistence import FileStateReader

    found: dict[str, list[Path]] = {}
    unidentified: list[Path] = []
    reader = FileStateReader()
    for path in iter_yaml_entry_files(entries_dir):
        lid = _read_learning_id(reader, path)
        if lid is None:
            logger.debug("user_yaml_entry_unidentified", reason="no_readable_id", path=str(path))
            unidentified.append(path)
        elif lid in user_ids:
            found.setdefault(lid, []).append(path)
    return found, unidentified


def user_yaml_row(target: Path, config: TRWConfig) -> tuple[Literal["PASS", "WARN", "SKIP"], str]:
    """``(status, message)``: WARN when a user-tier row also has a YAML copy in the project's entries dir."""
    from trw_mcp.state._store_selection import StoreUnavailableError

    trw_dir = target / ".trw"
    entries_dir = trw_dir / config.learnings_dir / config.entries_dir
    index = trw_dir / config.learnings_dir / "index.yaml"
    if not entries_dir.is_dir() and not index.is_file():
        return "PASS", f"{entries_dir} and {index} are absent; no project copy of a user-tier learning"
    try:
        user_ids = _user_tier_ids(trw_dir)
    except (StoreUnavailableError, ValueError) as exc:
        return "SKIP", f"user-tier copies not checked: {exc}"
    yaml_paths, unidentified = _copies_by_id(entries_dir, user_ids)
    copies = sorted(yaml_paths)
    indexed = _index_rows(index, user_ids)  # carries the summary too: a row left behind is still a copy
    if unidentified:
        names = ", ".join(path.name for path in unidentified[:_SHOWN_IDS])
        more = " ..." if len(unidentified) > _SHOWN_IDS else ""
        return "WARN", (
            f"inspection incomplete: {len(unidentified)} file(s) under {entries_dir} have no readable id "
            f"({names}{more}), so whether they hold a personal learning is unknown; git would publish them if "
            f"tracked. {len(copies)} user-tier copy(ies) and {indexed} index row(s) were found among the rest."
        )
    if not copies and not indexed:
        return "PASS", f"no user-tier learning has a copy under {entries_dir} or a row in {index}"
    shown = f" ({', '.join(copies[:_SHOWN_IDS])}{' ...' if len(copies) > _SHOWN_IDS else ''})" if copies else ""
    found = (
        f"{len(copies)} user-tier learning(s) have a copy under {entries_dir}{shown} and {indexed} row(s) in {index}"
    )
    real_paths = [*(path for lid in copies for path in yaml_paths[lid]), *([index] if indexed else [])]
    if _git_keeps_out_of_commits(target, real_paths):  # asks git about the real files, never the template (#157)
        return "PASS", (
            f"{found}; git ignores those files and none is tracked, so a commit would not publish them. They are "
            "duplicate storage only: the store holds each canonical row (delete the files and rows if you want them gone)."
        )
    return "WARN", (
        f"{found}; that folder is tracked by the installed gitignore, so a commit would publish them. The store "
        "holds each canonical row: delete those files and drop their rows from index.yaml (not done for you). "
        "A copy already committed stays in git history."
    )


def check_user_yaml(target: Path, config: TRWConfig) -> CheckResult:
    """Doctor-registry entry point (imported by name into _subcommands_doctor.py's globals)."""
    from trw_mcp.server._subcommands_doctor import CheckResult

    return CheckResult("user_tier_yaml", *user_yaml_row(target, config))
