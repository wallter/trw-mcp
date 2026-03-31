"""Version migration — predecessor cleanup and stale artifact removal.

Handles:
- PRD-FIX-032 predecessor skill/agent migration (non-prefixed -> trw- prefixed)
- Stale artifact removal based on manifest diffs
- Context transient cleanup during update-project
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import structlog

from ._utils import _result_action_key

logger = structlog.get_logger(__name__)

# Files in .trw/context/ that are always preserved during cleanup.
_CONTEXT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "analytics.yaml",
        "behavioral_protocol.yaml",
        "build-status.yaml",
        "messages.yaml",
        "pre_compact_state.json",
        "hooks-reference.yaml",
    }
)

# PRD-FIX-032: Maps old non-prefixed skill/agent names to their trw- successors.
# Used by _migrate_prefix_predecessors() to remove stale predecessors during
# update-project when the trw- prefixed successor is already installed.
PREDECESSOR_MAP: dict[str, dict[str, str | None]] = {
    "skills": {
        # PRD-FIX-032: Non-prefixed → trw- prefixed migration
        "audit": "trw-audit",
        "commit": "trw-commit",
        "deliver": "trw-deliver",
        "exec-plan": "trw-exec-plan",
        "framework-check": "trw-framework-check",
        "learn": "trw-learn",
        "memory-audit": "trw-memory-audit",
        "memory-optimize": "trw-memory-optimize",
        "prd-groom": "trw-prd-groom",
        "prd-new": "trw-prd-new",
        "prd-review": "trw-prd-review",
        "project-health": "trw-project-health",
        "review-pr": "trw-review-pr",
        "security-check": "trw-security-check",
        "simplify": "trw-simplify",
        "sprint-finish": "trw-sprint-finish",
        "sprint-init": "trw-sprint-init",
        "sprint-team": "trw-sprint-team",
        "team-playbook": "trw-team-playbook",
        "test-strategy": "trw-test-strategy",
        # PRD-CORE-092: Dropped skill post-consolidation
        "trw-review-pr": None,
    },
    "agents": {
        # PRD-FIX-032: Non-prefixed → trw- prefixed migration
        "code-simplifier.md": "trw-code-simplifier.md",
        "implementer.md": "trw-implementer.md",
        "lead.md": "trw-lead.md",
        "researcher.md": "trw-researcher.md",
        "reviewer.md": "trw-reviewer.md",
        "tester.md": "trw-tester.md",
        "adversarial-auditor.md": "trw-adversarial-auditor.md",
        "prd-groomer.md": "trw-prd-groomer.md",
        "requirement-reviewer.md": "trw-requirement-reviewer.md",
        "requirement-writer.md": "trw-requirement-writer.md",
        "traceability-checker.md": "trw-traceability-checker.md",
        # PRD-CORE-092: Dropped agents post-consolidation (18 → 5)
        "trw-tester.md": None,
        "trw-lead.md": None,
        "trw-code-simplifier.md": None,
        "trw-adversarial-auditor.md": "trw-auditor.md",
        "trw-traceability-checker.md": "trw-auditor.md",
        "trw-requirement-writer.md": "trw-prd-groomer.md",
        "trw-requirement-reviewer.md": "trw-prd-groomer.md",
        "reviewer-correctness.md": None,
        "reviewer-integration.md": None,
        "reviewer-performance.md": None,
        "reviewer-security.md": None,
        "reviewer-spec-compliance.md": None,
        "reviewer-style.md": None,
        "reviewer-test-quality.md": None,
    },
}


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------


def _coerce_manifest_list(value: object) -> list[str]:
    """Coerce a manifest field to ``list[str]``, returning ``[]`` for non-lists."""
    return [str(item) for item in value] if isinstance(value, list) else []


def _read_manifest(target_dir: Path) -> dict[str, object] | None:
    """Read the managed-artifacts manifest from a target project.

    Returns ``None`` if the manifest does not exist (first update after
    manifest support was added).
    """
    manifest_path = target_dir / ".trw" / _MANIFEST_FILE
    if not manifest_path.exists():
        return None
    try:
        from trw_mcp.state.persistence import FileStateReader

        reader = FileStateReader()
        data = reader.read_yaml(manifest_path)
        if not isinstance(data, dict):
            return None
        result: dict[str, object] = {
            key: _coerce_manifest_list(data.get(key, []))
            for key in (
                "skills",
                "agents",
                "hooks",
                "custom_skills",
                "custom_agents",
                "custom_hooks",
            )
        }
        result["version"] = int(data.get("version", 1))
        raw_hashes = data.get("content_hashes")
        if isinstance(raw_hashes, dict):
            result["content_hashes"] = {str(k): str(v) for k, v in raw_hashes.items()}
        else:
            result["content_hashes"] = {}
        return result
    except OSError:
        return None


def _compute_content_hashes(
    target_dir: Path,
    bundled: dict[str, list[str]],
) -> dict[str, str]:
    """Compute SHA256 hashes of installed artifact files.

    PRD-FIX-068-FR04: Hashes enable drift detection between installed
    copies and the current bundle.
    """
    hashes: dict[str, str] = {}
    mapping: list[tuple[str, str]] = [
        (".claude/agents", name) for name in bundled["agents"]
    ]
    mapping.extend(
        (".claude/hooks", name) for name in bundled["hooks"]
    )
    for subdir, name in mapping:
        path = target_dir / subdir / name
        try:
            if path.is_file():
                hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            logger.warning("content_hash_failed", path=str(path))
    # Skills are directories — hash the SKILL.md inside
    for name in bundled["skills"]:
        path = target_dir / ".claude" / "skills" / name / "SKILL.md"
        try:
            if path.is_file():
                hashes[f"{name}/SKILL.md"] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
        except OSError:
            logger.warning("content_hash_failed", path=str(path))
    return hashes


def _write_manifest(
    target_dir: Path,
    result: dict[str, list[str]],
    data_dir: Path | None = None,
) -> None:
    """Write the managed-artifacts manifest to the target project.

    The manifest records which skills, agents, and hooks were installed
    by TRW so that ``_remove_stale_artifacts`` can distinguish
    TRW-managed artifacts from user-created custom ones.

    PRD-FIX-068-FR04: Manifest version 2 includes SHA256 content hashes.
    """
    from ._template_updater import _get_bundled_names, _get_custom_names

    bundled = _get_bundled_names(data_dir)
    custom = _get_custom_names(target_dir, data_dir)
    # PRD-FIX-032-FR05: Exclude predecessor names from custom lists so they
    # are not permanently protected as false-custom entries.
    predecessor_skills = set(PREDECESSOR_MAP["skills"].keys())
    predecessor_agents = set(PREDECESSOR_MAP["agents"].keys())
    content_hashes = _compute_content_hashes(target_dir, bundled)
    manifest = {
        "version": 2,
        "skills": bundled["skills"],
        "agents": bundled["agents"],
        "hooks": bundled["hooks"],
        "content_hashes": content_hashes,
        "custom_skills": [s for s in custom["skills"] if s not in predecessor_skills],
        "custom_agents": [a for a in custom["agents"] if a not in predecessor_agents],
        "custom_hooks": custom["hooks"],
    }
    manifest_path = target_dir / ".trw" / _MANIFEST_FILE
    try:
        from trw_mcp.state.persistence import FileStateWriter

        writer = FileStateWriter()
        writer.write_yaml(manifest_path, manifest)
        key = _result_action_key(result)
        result[key].append(str(manifest_path))
    except OSError as exc:
        result["errors"].append(f"Failed to write manifest: {exc}")


_MANIFEST_FILE = "managed-artifacts.yaml"


# ---------------------------------------------------------------------------
# Context cleanup
# ---------------------------------------------------------------------------


def _cleanup_context_transients(
    target_dir: Path,
    result: dict[str, list[str]],
    dry_run: bool = False,
) -> None:
    """Remove transient artifacts from .trw/context/ during update-project.

    Preserves files in ``_CONTEXT_ALLOWLIST``.  Deletes everything else that
    is a regular file (not a directory, not a symlink).

    Args:
        target_dir: Root of the target git repository.
        result: Mutable result dict -- cleaned paths appended to ``result["cleaned"]``.
        dry_run: When ``True``, report what would be removed without deleting.
    """
    context_dir = target_dir / ".trw" / "context"
    if not context_dir.is_dir():
        return

    cleaned: list[str] = []
    for path in sorted(context_dir.iterdir()):
        # Skip symlinks first -- is_file() returns True for symlinks to files
        if path.is_symlink():
            continue
        if not path.is_file():
            continue
        if path.name in _CONTEXT_ALLOWLIST:
            continue
        if dry_run:
            result["cleaned"].append(f"would remove: {path}")
        else:
            try:
                path.unlink()
                result["cleaned"].append(str(path))
                cleaned.append(path.name)
            except OSError as exc:
                result["errors"].append(f"Failed to remove {path}: {exc}")

    logger.info(
        "context_cleanup",
        target=str(target_dir),
        cleaned_count=len(cleaned),
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# Predecessor migration
# ---------------------------------------------------------------------------


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
    skills_dir = target_dir / ".claude" / "skills"
    agents_dir = target_dir / ".claude" / "agents"

    _migrate_predecessor_set(
        skills_dir,
        PREDECESSOR_MAP["skills"],
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


# ---------------------------------------------------------------------------
# Stale artifact removal
# ---------------------------------------------------------------------------


def _remove_stale_set(
    stale_names: set[str],
    target_dir: Path,
    prev_custom: set[str],
    result: dict[str, list[str]],
    *,
    is_dir_artifact: bool,
    log_event: str,
    valid_prefixes: tuple[str, ...] | None = ("trw-",),
) -> None:
    """Remove a set of stale artifacts from *target_dir*.

    Skips names that are in *prev_custom* (user-created).  When
    *valid_prefixes* is not ``None``, also skips names that do not start
    with at least one of the specified prefixes (defense-in-depth for
    skills and agents).  Pass ``None`` to disable prefix filtering.

    Args:
        stale_names: Artifact names to consider for removal.
        target_dir: Directory containing the artifacts.
        prev_custom: Names from the previous manifest's custom list.
        result: Mutable result dict.
        is_dir_artifact: ``True`` to use ``shutil.rmtree``, ``False`` to use ``unlink``.
        log_event: structlog event name on removal failure.
        valid_prefixes: Tuple of allowed prefixes for stale removal.
            ``None`` disables prefix filtering entirely.
    """
    if not target_dir.is_dir():
        return
    for name in stale_names:
        if name in prev_custom:
            continue
        if valid_prefixes is not None and not name.startswith(valid_prefixes):
            continue
        stale = target_dir / name
        exists = stale.is_dir() if is_dir_artifact else stale.is_file()
        if not exists:
            continue
        try:
            if is_dir_artifact:
                shutil.rmtree(stale)
            else:
                stale.unlink()
            result["updated"].append(f"removed:{stale}")
        except OSError:
            logger.debug(log_event, path=str(stale), exc_info=True)


def _remove_stale_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    data_dir: Path | None = None,
) -> None:
    """Remove hooks/skills/agents that no longer exist in bundled data.

    Uses a manifest file (``.trw/managed-artifacts.yaml``) to track which
    artifacts were previously installed by TRW.  Only artifacts listed in
    the manifest are candidates for removal -- custom user-created
    artifacts are never touched.

    On the first update after manifest support is added, no stale cleanup
    is performed (the manifest is written for future updates).
    """
    from ._template_updater import _get_bundled_names

    prev_manifest = _read_manifest(target_dir)
    bundled = _get_bundled_names(data_dir)
    bundled_skills = set(bundled["skills"])
    bundled_agents = set(bundled["agents"])
    bundled_hooks = set(bundled["hooks"])

    if prev_manifest is None:
        # First run with manifest support -- write manifest, skip cleanup
        _write_manifest(target_dir, result, data_dir)
        return

    def _manifest_set(key: str) -> set[str]:
        val = prev_manifest.get(key)
        return set(_coerce_manifest_list(val)) if val else set()

    prev_skills = _manifest_set("skills")
    prev_agents = _manifest_set("agents")
    prev_hooks = _manifest_set("hooks")
    prev_custom_skills = _manifest_set("custom_skills")
    prev_custom_agents = _manifest_set("custom_agents")
    prev_custom_hooks = _manifest_set("custom_hooks")

    # Remove stale artifacts per category
    # Defense-in-depth: only remove trw-prefixed items to protect custom artifacts
    _remove_stale_set(
        stale_names=prev_skills - bundled_skills,
        target_dir=target_dir / ".claude" / "skills",
        prev_custom=prev_custom_skills,
        result=result,
        is_dir_artifact=True,
        log_event="stale_skill_removal_failed",
    )
    _remove_stale_set(
        stale_names=prev_agents - bundled_agents,
        target_dir=target_dir / ".claude" / "agents",
        prev_custom=prev_custom_agents,
        result=result,
        is_dir_artifact=False,
        log_event="stale_agent_removal_failed",
        valid_prefixes=("trw-", "reviewer-"),
    )
    _remove_stale_set(
        stale_names=prev_hooks - bundled_hooks,
        target_dir=target_dir / ".claude" / "hooks",
        prev_custom=prev_custom_hooks,
        result=result,
        is_dir_artifact=False,
        log_event="stale_hook_removal_failed",
        valid_prefixes=None,
    )

    # Write updated manifest
    _write_manifest(target_dir, result, data_dir)


# ---------------------------------------------------------------------------
# Stale artifact cleanup orchestrator
# ---------------------------------------------------------------------------


def _cleanup_stale_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    data_dir: Path | None,
    dry_run: bool,
) -> None:
    """Remove stale and transient artifacts after a framework update.

    Runs three cleanup passes in order:

    1. PRD-FIX-032: Migrate non-prefixed predecessor skills/agents to their
       ``trw-`` successors (safe: only removes old name when new name exists).
    2. Remove stale bundled artifacts (hooks/skills/agents that were previously
       managed by TRW but are no longer in the current bundle).
    3. Remove transient files from ``.trw/context/`` (cache/session files that
       should not persist across updates).

    Args:
        target_dir: Root of the target git repository.
        result: Mutable result dict accumulating ``updated``, ``cleaned``,
            and ``errors`` entries.
        data_dir: Optional override for the bundled data directory; passed
            through to ``_remove_stale_artifacts``.
        dry_run: When ``True``, report what would change without deleting files.
    """
    # PRD-FIX-032: Remove non-prefixed predecessors before stale cleanup
    _migrate_prefix_predecessors(target_dir, result, dry_run=dry_run)

    # Remove stale hooks/skills/agents no longer in bundled data
    if not dry_run:
        _remove_stale_artifacts(target_dir, result, data_dir)

    # Clean transient artifacts from .trw/context/
    _cleanup_context_transients(target_dir, result, dry_run=dry_run)
