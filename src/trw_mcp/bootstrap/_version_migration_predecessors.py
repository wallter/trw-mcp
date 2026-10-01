"""Predecessor migration helpers — extracted from _version_migration.py for module-size compliance.

Belongs to the ``_version_migration.py`` facade. Re-exported there for backward
compatibility with callers that import via the parent module
(``bootstrap/__init__.py``, ``test_bootstrap_branches_migration_cleanup.py``).

PRD-FIX-032: When projects upgrade past the trw- prefix migration, old
non-prefixed skill/agent artifacts need to be removed once their trw- successors
land. These helpers handle that cleanup with ownership proof (PRD-INFRA-190-FR06)
and error isolation.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import structlog

from ._safe_remove import remove_tree_if_hash

logger = structlog.get_logger(__name__)


def _migrate_predecessor_set(
    parent_dir: Path,
    name_map: dict[str, str | None],
    result: dict[str, list[str]],
    *,
    is_dir_artifact: bool,
    log_event: str,
    manifest_hashes: dict[str, str] | None,
    target_dir: Path,
) -> None:
    """Remove predecessor artifacts when their successor is installed or dropped.

    When *new_name* is ``None`` (PRD-CORE-092), the predecessor is removed
    without a successor. Either way a predecessor is deleted only with PROOF
    that TRW wrote it (PRD-FIX-139-FR01, PRD-INFRA-190-FR06): *manifest_hashes*
    is the pre-run manifest's ``content_hashes``, and every regular file under
    the artifact must be recorded there with its bytes unchanged. Anything else
    — no record, drifted content, or no manifest at all — is the project's own
    artifact and is preserved as ``not_installer_owned``.

    Args:
        parent_dir: Directory containing both predecessor and successor artifacts.
        name_map: Mapping of old (predecessor) name to new (successor) name,
            or ``None`` for deletion-only entries.
        result: Mutable result dict.
        is_dir_artifact: ``True`` for directory artifacts (skills), ``False`` for files (agents).
        log_event: structlog event name on removal failure.
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
        if preserve_unowned(predecessor, manifest_hashes, target_dir, result):
            continue
        remove_proven(predecessor, manifest_hashes, target_dir, result)


def preserve_unowned(
    artifact: Path, manifest_hashes: dict[str, str] | None, target_dir: Path, result: dict[str, list[str]]
) -> bool:
    """Report and keep *artifact* when TRW cannot prove it wrote it (FR06); True means keep."""
    if _trw_authored(artifact, manifest_hashes or {}, target_dir):
        return False
    rel = artifact.relative_to(target_dir).as_posix()
    result.setdefault("preserved", []).append(f"{rel} (not_installer_owned)")
    logger.info("sweep_removal_preserved", path=rel, reason="not_installer_owned")
    return True


def _trw_authored(artifact: Path, manifest_hashes: dict[str, str], target_dir: Path | None) -> bool:
    """True when every regular file under *artifact* hashes to its manifest record.

    Manifest keys are written in more than one shape (``<skill>/SKILL.md`` for
    ``.claude/skills``, ``.opencode/skills/<skill>/SKILL.md`` for opencode, bare
    ``<agent>.md`` for ``.claude/agents``), so a file is matched by any recorded
    key that equals its path relative to *target_dir* or a suffix of it at a
    path boundary. An unreadable file counts as unproven.
    """
    files = [f for f in sorted(artifact.rglob("*")) if f.is_file()] if artifact.is_dir() else [artifact]
    if not files:
        return False
    for path in files:
        recorded = recorded_digests(path, manifest_hashes, target_dir)
        if not recorded:
            return False
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() not in recorded:
                return False
        except OSError:  # trw-fail-silent-allow: unreadable means unproven; False preserves the artifact (the safe direction) and the warning above-the-fold reports it
            logger.warning("predecessor_authorship_unreadable", path=str(path), exc_info=True)
            return False
    return True


def recorded_digests(path: Path, manifest_hashes: dict[str, str], target_dir: Path | None) -> set[str]:
    """The manifest digests that prove *path* is TRW's (see :func:`_trw_authored` for the key shapes)."""
    rel = path.relative_to(target_dir).as_posix() if target_dir is not None else path.as_posix()
    # The exact manifest key alone decides. Suffix records (``x/SKILL.md`` is a
    # suffix of ``.agents/skills/x/SKILL.md`` but holds the claude-code bytes)
    # apply only when no exact key exists, so a suffix digest can never
    # authorize deleting a file whose own record says it was modified.
    if rel in manifest_hashes:
        return {manifest_hashes[rel]}
    return {digest for key, digest in manifest_hashes.items() if rel.endswith("/" + key)}


def remove_proven(
    artifact: Path, manifest_hashes: dict[str, str] | None, root: Path, result: dict[str, list[str]]
) -> None:
    """Remove a proven-stale *artifact* via :func:`remove_tree_if_hash`, re-proving every file at the act.

    The proof ``preserve_unowned`` took earlier is not trusted at delete time: each file is re-hashed and
    captured into ``.trw/trash``, so an edit saved in between (or a file added) keeps its bytes.
    """
    hashes = manifest_hashes or {}
    before = {f for f in artifact.rglob("*") if f.is_file() and not f.is_symlink()} if artifact.is_dir() else {artifact}
    kept = remove_tree_if_hash(artifact, root, lambda f: recorded_digests(f, hashes, root))
    # Every captured file is reported as trashed: the uncommitted-changes guard restores a dirty path it did not
    # see captured (so a retired skill came back in the client mirrors the user had touched), and the CLI names
    # only what is listed here (the nine silent skill files in feedback sub_i7UMmxUbTbsdW0eD, FB-INSTALL-03).
    result.setdefault("trashed", []).extend(
        f.relative_to(root).as_posix() for f in sorted(before) if not os.path.lexists(f)
    )
    # Warnings are what the CLI prints (preserved is summarised as a count); a kept file must say why.
    result.setdefault("warnings", []).extend(f"{why}: kept" for why in kept)


def _migrate_prefix_predecessors(
    target_dir: Path,
    result: dict[str, list[str]],
    manifest_hashes: dict[str, str] | None = None,
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
    from trw_mcp.bootstrap._version_migration import PREDECESSOR_MAP, RELOCATED_CLIENT_AGENTS

    agents_dir = target_dir / ".claude" / "agents"

    skill_map = PREDECESSOR_MAP["skills"]
    _migrate_predecessor_set(
        target_dir / ".claude" / "skills",
        skill_map,
        result,
        is_dir_artifact=True,
        log_event="predecessor_skill_removal_failed",
        manifest_hashes=manifest_hashes,
        target_dir=target_dir,
    )

    # PRD-FIX-139-FR03: a client mirror follows its source. When the canonical
    # ``.claude/skills/<name>`` survived the pass above (preserved as the
    # project's own artifact), the mirrors TRW rendered FROM it are not stale
    # either — deleting them would leave the source without its projections
    # and re-create the churn the preservation exists to stop.
    retired_skills: dict[str, str | None] = {
        name: None
        for name, successor in skill_map.items()
        if successor is None and not (target_dir / ".claude" / "skills" / name).is_dir()
    }
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
            manifest_hashes=manifest_hashes,
            target_dir=target_dir,
        )
    _migrate_predecessor_set(
        agents_dir,
        PREDECESSOR_MAP["agents"],
        result,
        is_dir_artifact=False,
        log_event="predecessor_agent_removal_failed",
        manifest_hashes=manifest_hashes,
        target_dir=target_dir,
    )

    # Retired agent NAMES follow the client trees too, the same way retired
    # skills do above: a name dropped from the bundle must not survive in the
    # five per-client destinations that also received it.
    retired_agents = {name for name, successor in PREDECESSOR_MAP["agents"].items() if successor is None}
    for client_agents_dir, suffix in _client_agent_roots(target_dir):
        _migrate_predecessor_set(
            client_agents_dir,
            {f"{name.removesuffix('.md')}{suffix}": None for name in retired_agents},
            result,
            is_dir_artifact=False,
            log_event="predecessor_agent_removal_failed",
            manifest_hashes=manifest_hashes,
            target_dir=target_dir,
        )

    # Directories TRW no longer writes to at all, swept by exact filename.
    for rel_dir, filenames in RELOCATED_CLIENT_AGENTS.items():
        _migrate_predecessor_set(
            target_dir / rel_dir,
            dict.fromkeys(filenames),
            result,
            is_dir_artifact=False,
            log_event="relocated_agent_removal_failed",
            manifest_hashes=manifest_hashes,
            target_dir=target_dir,
        )


def _client_agent_roots(target_dir: Path) -> list[tuple[Path, str]]:
    """``(destination directory, filename suffix)`` per agent-capable client.

    Derived from the FR01 format registry so a client added there is swept the
    day it lands, rather than the day someone notices — the same reason the
    agent-contract linter reads the profile catalog instead of keeping its own
    list.
    """
    from trw_mcp.agents.agent_formats import agent_format_for
    from trw_mcp.agents.tier_resolver import KNOWN_CLIENTS

    roots: list[tuple[Path, str]] = []
    for client in sorted(KNOWN_CLIENTS):
        fmt = agent_format_for(client)
        if fmt.supports_agents and fmt.destination_dir is not None and fmt.destination_dir != ".claude/agents":
            roots.append((target_dir / fmt.destination_dir, fmt.filename_suffix))
    return roots
