"""Deletion tombstones — PRD-INFRA-192 FR10/FR12.

A manifest-tracked path the user deletes stays deleted across init/update:
only an explicit re-provision request clears it. Detection runs once, before
any writer, against the manifest as it stood at the start of the run;
enforcement runs once, after every writer and before the manifest is
(re)written, undoing any writer that recreated a tombstoned path this run.

Belongs to the ``_update_project.py`` / ``_init_project.py`` transactions.
Kept as its own module so those facades stay under the 350 effective-LOC gate.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ._hook_deregistration import deregister_hook_script
from ._version_manifest import _manifest_key_path

_HOOKS_DIR_PREFIX = ".claude/hooks/"


def _tombstone_target(target_dir: Path, key: str) -> Path:
    """Repo-relative path a ``content_hashes`` *key* names, resolved under *target_dir*.

    A ``.../SKILL.md`` key (bare claude-code form or a leading-dot client
    form) names the WHOLE skill directory: every other file in it was written
    alongside ``SKILL.md`` by the same install/update, so unlinking just the
    one file would leave siblings behind and the directory non-empty.
    """
    rel = _manifest_key_path(key)
    path = target_dir / rel
    return path.parent if rel.endswith("/SKILL.md") else path


def _tombstone_key_exists(target_dir: Path, key: str) -> bool:
    """Whether the manifest-RECORDED FILE itself exists — never the directory substitution.

    A ``.../SKILL.md`` key names the whole directory for DELETION purposes
    (:func:`_tombstone_target`), but for deciding whether the key is
    tombstoned only the recorded file matters: a skill directory that still
    holds sibling files the user kept is not evidence the user restored the
    skill — only the file TRW actually tracks (``SKILL.md``) is.
    """
    return (target_dir / _manifest_key_path(key)).is_file()


def detect_tombstones(target_dir: Path, prev_manifest: dict[str, object] | None) -> set[str]:
    """The tombstone set effective at the start of this run.

    ``prev tombstones ∪ {key in prev content_hashes whose path is absent}``,
    minus any entry whose path exists right now: an existing path means the
    user restored it themselves, so it is handed back to the normal guarded
    writer instead of staying tombstoned. A key absent from the prior
    manifest's ``content_hashes`` was never provisioned and is never
    tombstoned here — a newly bundled artifact is written normally.
    """
    if not isinstance(prev_manifest, dict):
        return set()
    raw_tombstones = prev_manifest.get("tombstones")
    prev_tombstones = (
        {str(k) for k in raw_tombstones if isinstance(k, str)} if isinstance(raw_tombstones, list) else set()
    )
    prev_hashes = prev_manifest.get("content_hashes")
    newly_absent: set[str] = set()
    if isinstance(prev_hashes, dict):
        newly_absent = {str(k) for k in prev_hashes if not _tombstone_key_exists(target_dir, str(k))}
    candidate = prev_tombstones | newly_absent
    return {key for key in candidate if not _tombstone_key_exists(target_dir, key)}


def apply_reprovision(tombstones: set[str], reprovision: Sequence[str] | None) -> tuple[set[str], list[str]]:
    """Remove explicitly re-provisioned entries from *tombstones*.

    A named entry matches by its ``content_hashes`` key or its repo-relative
    path (a leading ``./`` is tolerated). ``"all"`` clears every tombstone.
    An entry matching nothing is an error naming it — the caller must then
    perform no writes at all, so a typo cannot silently no-op (FR10).
    """
    if not reprovision:
        return set(tombstones), []
    if "all" in reprovision:
        return set(), []
    remaining = set(tombstones)
    errors: list[str] = []
    for raw in reprovision:
        normalized = raw.strip().removeprefix("./")
        matched = {key for key in tombstones if key in (raw, normalized) or _manifest_key_path(key) == normalized}
        if not matched:
            errors.append(f"--reprovision {raw!r} does not match a tombstoned path")
            continue
        remaining -= matched
    return remaining, errors


def resolve_run_tombstones(
    target_dir: Path,
    prev_manifest: dict[str, object] | None,
    reprovision: Sequence[str] | None,
) -> tuple[set[str], list[str]]:
    """Detection + reprovision in one call, for the ``update_project`` entry point."""
    return apply_reprovision(detect_tombstones(target_dir, prev_manifest), reprovision)


def prepare_update_manifest_state(
    target_dir: Path,
    reprovision: Sequence[str] | None,
    result: dict[str, list[str]],
) -> tuple[dict[str, str] | None, set[str], dict[str, frozenset[str]]] | None:
    """``(manifest_hashes, tombstones, skill_dir_snapshot)`` for ``update_project``.

    ``None`` on an unknown ``--reprovision``: *result* already carries the
    reprovision error(s), and the caller must return *result* immediately,
    with no writer having run (FR10). One call for the prior-manifest state
    ``update_project`` needs before any writer runs, so its own body stays
    inside the 350 effective-LOC gate. ``skill_dir_snapshot`` is captured here
    too — it must exist before any writer runs, same as *tombstones* itself.
    """
    from ._skill_tombstone_prune import snapshot_skill_dir_siblings
    from ._version_manifest import _manifest_content_hashes, _read_manifest

    prev_manifest = _read_manifest(target_dir)
    tombstones, errors = resolve_run_tombstones(target_dir, prev_manifest, reprovision)
    if errors:
        result["errors"].extend(errors)
        return None
    return (
        _manifest_content_hashes(prev_manifest),
        tombstones,
        snapshot_skill_dir_siblings(target_dir, tombstones),
    )


def enforce_and_write_manifest(
    root: Path,
    result: dict[str, list[str]],
    tombstones: set[str],
    effective_data: Path,
    clients: list[str],
    skill_dir_snapshot: dict[str, frozenset[str]] | None = None,
) -> None:
    """Enforce tombstones, then write the manifest recording them (FR10/FR12).

    One call for ``_apply_update``'s last two writer-adjacent steps, so its own
    body stays inside the 350 effective-LOC gate.
    """
    from ._version_migration import _write_manifest

    enforce_tombstones(root, tombstones, result, skill_dir_snapshot)
    _write_manifest(root, result, effective_data, clients=clients, tombstones=tombstones)


def enforce_tombstones(
    target_dir: Path,
    tombstones: set[str],
    result: dict[str, list[str]],
    skill_dir_snapshot: dict[str, frozenset[str]] | None = None,
) -> None:
    """Undo any writer that recreated a tombstoned path during this run.

    Every entry in *tombstones* was absent at the start of the run (by
    construction of :func:`detect_tombstones`/:func:`apply_reprovision`), so
    one that exists now was written by a writer THIS run. For a plain file
    target that means the whole file is this run's own output and safe to
    remove; for a ``.../SKILL.md`` DIRECTORY target it does NOT mean every
    byte under it is this run's output — a sibling file the user kept
    predates this run, so only files absent from *skill_dir_snapshot* (this
    key's pre-run snapshot) are removed. A deletion failure is appended to
    ``result["errors"]``, which rolls the whole transaction back.

    A tombstoned hook script also gets its dangling registrations removed
    from every registration surface (``.claude/settings.json``,
    ``.codex/hooks.json``, the Copilot hooks file), every run — not only when
    a writer recreated the script this run, since a hook deleted before this
    enforcement pass existed may already carry a dangling registration.
    """
    from ._skill_tombstone_prune import prune_recreated_skill_dir

    snapshot = skill_dir_snapshot or {}
    for key in sorted(tombstones):
        rel = _manifest_key_path(key)
        if rel.startswith(_HOOKS_DIR_PREFIX):
            deregister_hook_script(target_dir, rel, result)
        target = _tombstone_target(target_dir, key)
        if not target.exists():
            continue
        try:
            if target.is_dir():
                prune_recreated_skill_dir(target, snapshot.get(key, frozenset()))
            else:
                target.unlink()
            result.setdefault("info", []).append(
                f"kept deleted: {_manifest_key_path(key)} (update-project --reprovision restores it)"
            )
        except OSError as exc:
            result["errors"].append(f"Failed to enforce tombstone for {key!r}: {exc}")
