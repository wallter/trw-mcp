"""Predecessor migration helpers — extracted from _version_migration.py for module-size compliance.

Belongs to the ``_version_migration.py`` facade. Re-exported there for backward
compatibility with callers that import via the parent module
(``bootstrap/__init__.py``, ``test_bootstrap_branches_migration_cleanup.py``).

PRD-FIX-032: When projects upgrade past the trw- prefix migration, old
non-prefixed skill/agent artifacts need to be removed once their trw- successors
land. These helpers handle that cleanup with appropriate dry-run + error
isolation guarantees.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


def _migrate_predecessor_set(
    parent_dir: Path,
    name_map: dict[str, str | None],
    result: dict[str, list[str]],
    *,
    is_dir_artifact: bool,
    log_event: str,
    dry_run: bool,
) -> None:
    """Remove predecessor artifacts when their successor is installed or dropped.

    When *new_name* is ``None`` (PRD-CORE-092), the predecessor is removed
    unconditionally (deletion-only, no successor required).

    Args:
        parent_dir: Directory containing both predecessor and successor artifacts.
        name_map: Mapping of old (predecessor) name to new (successor) name,
            or ``None`` for deletion-only entries.
        result: Mutable result dict.
        is_dir_artifact: ``True`` for directory artifacts (skills), ``False`` for files (agents).
        log_event: structlog event name on removal failure.
        dry_run: When ``True``, only report without deleting.
    """
    for old_name, new_name in name_map.items():
        predecessor = parent_dir / old_name
        # Check predecessor exists
        if is_dir_artifact:
            if not predecessor.is_dir():
                continue
        else:
            if not predecessor.is_file():
                continue
        # When new_name is not None, require successor to exist before removing
        if new_name is not None:
            successor = parent_dir / new_name
            if is_dir_artifact:
                if not successor.is_dir():
                    continue
            else:
                if not successor.is_file():
                    continue
        if dry_run:
            result["updated"].append(f"would migrate:{predecessor}")
            continue
        try:
            if is_dir_artifact:
                shutil.rmtree(predecessor)
            else:
                predecessor.unlink()
            result["updated"].append(f"migrated:{predecessor}")
        except OSError:
            logger.debug(log_event, path=str(predecessor), exc_info=True)


def _migrate_prefix_predecessors(
    target_dir: Path,
    result: dict[str, list[str]],
    dry_run: bool = False,
) -> None:
    """Remove non-prefixed predecessor skills/agents when trw- successor is installed.

    PRD-FIX-032: Projects initialized before the trw- prefix migration
    (PRD-INFRA-013) still have old non-prefixed skill directories and agent
    files.  This function removes them only when the trw- prefixed successor
    is already present, ensuring no data loss.

    This function is intended for ``update_project()`` only.  It is called
    before ``_remove_stale_artifacts()`` so the manifest written afterwards
    is already clean of predecessor entries.
    """
    # Lazy-import PREDECESSOR_MAP from parent to avoid circular dep at import time.
    from trw_mcp.bootstrap._version_migration import PREDECESSOR_MAP

    agents_dir = target_dir / ".claude" / "agents"

    skill_map = PREDECESSOR_MAP["skills"]
    _migrate_predecessor_set(
        target_dir / ".claude" / "skills",
        skill_map,
        result,
        is_dir_artifact=True,
        log_event="predecessor_skill_removal_failed",
        dry_run=dry_run,
    )

    retired_skills: dict[str, str | None] = {name: None for name, successor in skill_map.items() if successor is None}
    client_skill_roots = (
        target_dir / ".agents" / "skills",
        target_dir / ".cursor" / "skills",
        target_dir / ".github" / "skills",
        target_dir / ".opencode" / "skills",
    )
    for skills_dir in client_skill_roots:
        _migrate_predecessor_set(
            skills_dir,
            retired_skills,
            result,
            is_dir_artifact=True,
            log_event="predecessor_skill_removal_failed",
            dry_run=dry_run,
        )
    _migrate_predecessor_set(
        agents_dir,
        PREDECESSOR_MAP["agents"],
        result,
        is_dir_artifact=False,
        log_event="predecessor_agent_removal_failed",
        dry_run=dry_run,
    )
