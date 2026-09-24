"""Skill-directory tombstone pruning — PRD-INFRA-192 FR10 follow-up.

A ``.../SKILL.md`` key names the whole skill directory for deletion purposes
(every other file in it was written alongside ``SKILL.md`` by the same
install/update), but the directory can also hold files the user placed there
that TRW never wrote — a sibling file surviving a ``SKILL.md`` deletion is not
this run's output. Enforcement must remove only what THIS run's writer
recreated under that directory: the standing rule is that TRW never removes a
byte it can't prove it wrote this run or that is unchanged from what it
recorded.

Belongs to the ``_tombstones.py`` enforcement pass; split out to keep that
module — and the 350 effective-LOC-gated ``_update_project.py`` /
``_init_project.py`` facades it serves — small.
"""

from __future__ import annotations

from pathlib import Path


def snapshot_skill_dir_siblings(target_dir: Path, tombstones: set[str]) -> dict[str, frozenset[str]]:
    """Relative file paths already present under each tombstoned skill directory.

    Must be captured before any writer runs this run (right after tombstone
    resolution, alongside :func:`trw_mcp.bootstrap._tombstones.detect_tombstones`
    / ``apply_reprovision``). A key whose target is not a ``.../SKILL.md``
    directory has no entry — a plain file tombstone needs no snapshot, since
    removing the exact recorded file is already exact.
    """
    from ._version_manifest import _manifest_key_path

    snapshot: dict[str, frozenset[str]] = {}
    for key in tombstones:
        rel = _manifest_key_path(key)
        if not rel.endswith("/SKILL.md"):
            continue
        directory = (target_dir / rel).parent
        if directory.is_dir():
            snapshot[key] = frozenset(str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file())
        else:
            snapshot[key] = frozenset()
    return snapshot


def prune_recreated_skill_dir(directory: Path, pre_existing: frozenset[str]) -> None:
    """Remove only the files THIS run's writer created under *directory*.

    *pre_existing* is the sibling-relative-path snapshot taken before this
    run's writers ran (empty when the directory did not exist at that point,
    in which case every file found now was created this run and is safe to
    remove wholesale). The directory itself, and any subdirectory left empty
    by the removal, is pruned too — but only when it is actually empty,
    never forced, so a surviving sibling keeps its directory alive.
    """
    for path in sorted((p for p in directory.rglob("*") if p.is_file()), key=lambda p: -len(p.parts)):
        if str(path.relative_to(directory)) not in pre_existing:
            path.unlink()
    for sub in sorted((p for p in directory.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        if not any(sub.iterdir()):
            sub.rmdir()
    if directory.is_dir() and not any(directory.iterdir()):
        directory.rmdir()
