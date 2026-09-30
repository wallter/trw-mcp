"""Manifest-driven per-client uninstall (PRD-INFRA-192 FR09).

Belongs to the ``server/_subcommands_lifecycle.py`` ``_run_uninstall`` handler.

Before this module, ``uninstall --ide X`` deleted every plain catalog
directory X declared (``.claude/skills``, ``.codex/agents``, ...) wholesale
with ``shutil.rmtree`` -- including a user's own custom skill dropped into
that directory, or a TRW file the user had hand-edited, neither of which TRW
has any right to destroy.

This module replaces that wholesale delete, for the subset of surfaces the
manifest's ``content_hashes``/``owners`` maps actually track: a recorded file
is deleted only when (a) no client that remains recorded after this removal
still owns it, and (b) its on-disk bytes still match the hash TRW recorded --
i.e. the user never touched it. Everything else under the directory (a custom
skill, an edited copy) is left exactly where it is. A plain surface the
manifest never recorded anything under (a recorder-coverage gap -- e.g.
``.claude/loop.md``, ``.grok/agents``: no recorder in ``_manifest_recorders.py``
covers them) is not this module's concern; the caller keeps the old wholesale
behavior for those, unchanged.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import structlog

from ._safe_remove import _SHA256_RE, path_refusal, remove_if_hash
from ._uninstall_skill_dir import (
    _RERUN_CAN_CHANGE as _RERUN_CAN_CHANGE,
)
from ._uninstall_skill_dir import (
    _TOO_LARGE as _TOO_LARGE,
)
from ._uninstall_skill_dir import (
    _hash_regular_file as _hash_regular_file,
)
from ._uninstall_skill_dir import (
    _lexists_strict as _lexists_strict,
)
from ._uninstall_skill_dir import (
    _remove_skill_dir as _remove_skill_dir,
)
from ._uninstall_skill_dir import (
    prune_empty_dirs as prune_empty_dirs,
)

logger = structlog.get_logger(__name__)

#: The four dispositions a manifest-tracked key can land on, in the vocabulary
#: ``--dry-run`` prints and ``TestUninstall*`` asserts on.
Action = str  # "remove" | "preserved-edited" | "kept-shared-owner" | "rejected-unsafe"


@dataclass(frozen=True, slots=True)
class KeyDisposition:
    """One content_hashes key's fate under a scoped ``uninstall --ide``."""

    key: str
    path: Path
    action: Action
    detail: str = ""
    is_dir: bool = False
    recorded_hash: str = ""


#: Printed for every plain surface rule 3 keeps (PRD-INFRA-192 FR09 C3): no
#: content_hashes key exists under it, so TRW cannot prove which of its
#: contents (if any) are its own unedited writes.
_UNCOVERED_NOTE = "not recorded by TRW; left in place — run update-project first to record it"


@dataclass(frozen=True, slots=True)
class SurfaceDisposition:
    """One plain surface's fate when the manifest has NO key under it (FR09 C3 rule 3)."""

    relpath: str
    path: Path
    action: str  # "remove" | "kept" | "refused" (path_refusal said no: nothing was judged, residue may be TRW's)
    detail: str = ""
    judged_sha256: str = ""  # the bytes a "remove" was judged on; the delete re-proves them (codex r2 P0)


def plan_uncovered_surface(path: Path, relpath: str, root: Path) -> SurfaceDisposition:
    """Decide the fate of a plain client surface the manifest covers with NO key.

    ``content_hashes`` has no entry under *relpath* either because no recorder
    has ever covered it, or because every file under it was user-edited (a
    recorder declines to record an edit) -- both cases mean TRW cannot tell
    which of the surface's contents, if any, are its own unedited writes. A
    directory is therefore always kept whole, never ``rmtree``'d: rule 1 of
    PRD-INFRA-192 FR09 C3 (never delete a byte TRW cannot prove it wrote and
    that is unchanged). A single-file surface is removed only when its bytes
    are still byte-identical to what TRW would write today, checked generically
    against every registered content source rather than a per-file literal in
    the caller, AND *path* passes :func:`_safe_remove.path_refusal` against
    *root* -- a symlinked "file" surface is refused outright rather than
    unlinked, same rule as every other deletion path in this module.
    """
    refusal = path_refusal(path, root)
    if refusal:
        return SurfaceDisposition(relpath, path, "refused", refusal)
    if path.is_dir():
        return SurfaceDisposition(relpath, path, "kept", _UNCOVERED_NOTE)
    from ._managed_client_artifacts import bundled_content_for

    bundled = bundled_content_for(relpath)
    if bundled is None:
        return SurfaceDisposition(relpath, path, "kept", _UNCOVERED_NOTE)
    try:
        current = path.read_bytes()
    except OSError:  # trw-fail-silent-allow: an unreadable file cannot be proven unedited, so it is kept
        return SurfaceDisposition(relpath, path, "kept", "unreadable; left in place")
    if current == bundled or (relpath == "REVIEW.md" and _is_generated_review_md(current, root)):
        # REVIEW.md is regenerated with the project's learnings on every sync, so its bytes are proven against
        # the hash of TRW's last write (INC-080); any user edit, even in TRW's line format, keeps it.
        return SurfaceDisposition(relpath, path, "remove", "", hashlib.sha256(current).hexdigest())
    return SurfaceDisposition(relpath, path, "kept", _UNCOVERED_NOTE)


def remove_judged_surface(u: SurfaceDisposition, target: Path, captures: dict[str, list[str]]) -> tuple[str, str]:
    """Delete a planned surface only if its bytes still hash to what planning judged: ``(status, detail)``.

    *status* is "removed", "kept" (the bytes changed since planning: the user's now) or "error".

    Planning and the delete are separated by the confirmation prompt, so an edit saved in between must
    survive (HB-2): ``remove_if_hash`` captures the file, re-hashes the captured bytes and links them back
    on any mismatch. Runtime caller: ``_subcommands_lifecycle`` for each uncovered "remove" disposition.
    """
    refusal = path_refusal(u.path, target)
    if refusal:
        return "error", refusal
    outcome = remove_if_hash(u.path, target, u.judged_sha256, key=u.relpath)
    if outcome.status == "removed":  # the capture joins apply_removal's, for the same move to the system Trash
        captures.setdefault("trashed", []).append(str(u.path))
        captures.setdefault("trashed_at", []).append(str(outcome.retained_at or ""))
    if outcome.status in ("removed", "absent"):
        return "removed", ""
    edited = outcome.status == "kept" and outcome.reason.startswith("bytes differ")
    if edited:
        return "kept", "edited since the plan; left in place"
    # codex r3 KI: name where the bytes are when they are not at the path, as apply_removal does.
    where = f"; a copy is in {outcome.retained_at}" if outcome.retained_at is not None else ""
    return "error", f"kept ({outcome.reason}){where}"


def _is_generated_review_md(current: bytes, root: Path) -> bool:
    from trw_mcp.state.claude_md._review_md import is_generated_review_md

    try:
        return is_generated_review_md(current.decode("utf-8"), root / ".trw")
    except UnicodeDecodeError:  # trw-fail-silent-allow: undecodable bytes are not TRW's text, so the file is kept
        return False


def manifest_covers_surface(content_hashes: dict[str, str], surface_relpath: str) -> bool:
    """True when some *content_hashes* key resolves under *surface_relpath*."""
    from ._version_manifest import _manifest_key_path

    for key in content_hashes:
        path = _manifest_key_path(key)
        if path == surface_relpath or path.startswith(f"{surface_relpath}/"):
            return True
    return False


def _deletion_target(resolved: Path) -> tuple[Path, bool]:
    """The ``(path, is_dir)`` unit actually removed for one recorded key.

    A ``SKILL.md`` key names its skill directory: the recorder hashes only
    ``SKILL.md`` (PRD-FIX-121), so the directory's other files are judged
    against the bundled skill in :func:`_remove_skill_dir`, never rmtree'd.
    Every other recorded key (an agent or hook file) names exactly the file
    to delete -- it has no siblings the recorder omits.
    """
    if resolved.name == "SKILL.md":
        return resolved.parent, True
    return resolved, False


def plan_manifest_removal(
    target: Path,
    surface_relpath: str,
    remove_ide: str,
    content_hashes: dict[str, str],
    owners: dict[str, list[str]],
    remaining_targets: list[str],
) -> list[KeyDisposition]:
    """Per-key disposition for every ``content_hashes`` key under *surface_relpath*.

    A key already gone from disk (a prior run's removal, or the file never
    existed) yields no disposition at all -- that is what makes a repeated
    removal idempotent (FR09 §2.f).
    """
    from ._version_manifest import _manifest_key_path

    surface_root = target / surface_relpath
    dispositions: list[KeyDisposition] = []
    for key, recorded_hash in sorted(content_hashes.items()):
        rel = _manifest_key_path(key)
        if rel != surface_relpath and not rel.startswith(f"{surface_relpath}/"):
            continue
        resolved = target / rel
        delete_path, is_dir = _deletion_target(resolved)
        # Both the recorded key path itself (e.g. a symlinked SKILL.md) and,
        # for a directory deletion, its containing dir must be checked -- a
        # SKILL.md key's delete target is its PARENT dir, which a symlinked
        # SKILL.md alone would not catch.
        refusal = path_refusal(resolved, target) or (path_refusal(delete_path, target) if is_dir else None)
        if not refusal:
            # A key must resolve within ITS OWN declared surface, not merely
            # somewhere in the project -- a "../.." key that cancels back out
            # to inside the project root but outside `surface_relpath` (e.g.
            # ``.claude/skills/../../<sibling-name>``) must still be refused.
            try:
                resolved.resolve().relative_to(surface_root.resolve())
            except (OSError, ValueError):
                refusal = "refused: path resolves outside its surface"
        if refusal:
            dispositions.append(KeyDisposition(key, delete_path, "rejected-unsafe", refusal))
            continue
        key_owners = owners.get(key, [])
        remaining_owners = [o for o in key_owners if o != remove_ide]
        if any(o in remaining_targets for o in remaining_owners):
            dispositions.append(KeyDisposition(key, delete_path, "kept-shared-owner", f"owned by {remaining_owners}"))
            continue
        if is_dir and not _lexists_strict(resolved):
            # SKILL.md is gone (an earlier run deleted it, keeping the record for a TRW-owned sibling): a directory
            # still there is re-evaluated so the record can converge; both gone means nothing is left to track.
            if _lexists_strict(delete_path) and not delete_path.is_symlink() and delete_path.is_dir():
                dispositions.append(KeyDisposition(key, delete_path, "remove", "", True, recorded_hash))
            elif not _lexists_strict(delete_path):
                dispositions.append(KeyDisposition(key, delete_path, "remove", "already gone", True, recorded_hash))
            continue
        if not resolved.is_file():
            continue
        current_hash, why = _hash_regular_file(resolved)
        if current_hash is None and why != _TOO_LARGE:
            dispositions.append(KeyDisposition(key, delete_path, "rejected-unsafe", "unreadable"))
            continue
        if current_hash != recorded_hash:
            dispositions.append(KeyDisposition(key, delete_path, "preserved-edited", "", is_dir))
            continue
        dispositions.append(KeyDisposition(key, delete_path, "remove", "", is_dir, recorded_hash))
    return dispositions


def apply_removal(
    dispositions: list[KeyDisposition], result: dict[str, list[str]], target: Path
) -> tuple[set[str], int]:
    """Execute every ``remove`` disposition. Returns ``(removed_keys, error_count)``.

    *removed_keys* feeds the manifest rewrite (dropped from ``content_hashes``/
    ``owners``); it only ever contains keys that were ACTUALLY deleted, so a
    failed unlink keeps its manifest record and a retry sees it again (FR09 §2.f).

    Re-checks :func:`_safe_remove.path_refusal` against *target* immediately
    before deleting, even though planning already checked it -- a defense
    against a TOCTOU window between plan and execute (e.g. a path replaced
    with a symlink between the two), not a redundant no-op.
    """
    removed_keys: set[str] = set()
    errors = 0
    # Files with their own manifest key (e.g. a client's generated ``*-contract.md``) are decided by that key's
    # disposition, which runs BEFORE any skill directory so the directory sees what is really left on disk.
    own_keys = {d.path: d.action for d in dispositions if not d.is_dir}
    for d in sorted(dispositions, key=lambda d: d.is_dir):
        if d.action == "preserved-edited":
            result.setdefault("preserved", []).append(f"{d.path} (edited)")
            continue
        if d.action != "remove":
            continue
        try:
            if d.is_dir:
                refusal = path_refusal(d.path, target)
                if refusal:
                    errors += 1
                    result.setdefault("errors", []).append(f"{d.path}: {refusal}")
                    continue
                kept, failures, trw_left = _remove_skill_dir(d.path, target, d.recorded_hash, own_keys, result)
                # A kept file that has its own disposition was already reported by it.
                result.setdefault("preserved", []).extend(
                    f"{path} ({why})" for path, why in kept if path not in own_keys
                )
                for path, why in failures:
                    errors += 1
                    result.setdefault("errors", []).append(f"{path}: {why}")
                if any(why in _RERUN_CAN_CHANGE for _path, why in kept if _path not in own_keys):
                    result.setdefault("trw_left", []).append(str(d.path))
                if trw_left or failures:
                    # A TRW-owned file remains (edited, unreadable) or a delete failed: the record stays so a
                    # rerun still finds it. Only user-owned leftovers mean nothing is left to track.
                    continue
            else:
                # Planning hashed the file; an edit saved since must survive (HB-2). remove_if_hash captures
                # the file into .trw/trash, re-hashes THOSE bytes and links them back on a mismatch; it never
                # unlinks. removed/absent drop the record; kept/retained keep it so a rerun sees it again.
                refusal = path_refusal(d.path, target)  # the documented re-check right before the act
                if refusal:
                    errors += 1
                    result.setdefault("errors", []).append(f"{d.path}: {refusal}")
                    continue
                if _SHA256_RE.fullmatch(d.recorded_hash) is None:
                    errors += 1
                    result.setdefault("errors", []).append(f"{d.path}: kept (no valid recorded hash)")
                    continue
                outcome = remove_if_hash(d.path, target, d.recorded_hash, key=d.key)
                if outcome.status == "removed":
                    # ``trashed_at`` stays index-aligned with ``trashed`` ("" when the capture is not visible) so
                    # uninstall can move these captures on to the system Trash.
                    result.setdefault("trashed", []).append(str(d.path))
                    result.setdefault("trashed_at", []).append(str(outcome.retained_at or ""))
                elif outcome.status == "kept" and outcome.published is None and outcome.retained_at is not None:
                    # The folder moved mid-removal: the bytes are not at d.path; say where a copy is.
                    errors += 1
                    result.setdefault("errors", []).append(
                        f"{d.path}: kept ({outcome.reason}); a copy is in {outcome.retained_at}"
                    )
                    continue
                elif outcome.status == "kept" and outcome.reason.startswith("bytes differ"):
                    result.setdefault("preserved", []).append(f"{d.path} (edited: {outcome.reason})")
                    continue
                elif outcome.status in ("kept", "retained"):
                    where = f"; your bytes are in {outcome.retained_at}" if outcome.status == "retained" else ""
                    errors += 1
                    result.setdefault("errors", []).append(f"{d.path}: kept ({outcome.reason}){where}")
                    continue
            removed_keys.add(d.key)
        except OSError as exc:
            errors += 1
            result.setdefault("errors", []).append(f"{d.path}: {exc}")
    return removed_keys, errors


def rewrite_manifest_after_removal(target: Path, removed_keys: set[str], remove_ide: str) -> None:
    """Atomically drop *removed_keys* and *remove_ide* from the manifest (FR09 §2.e)."""
    from trw_mcp.exceptions import StateError
    from trw_mcp.state.persistence import FileStateReader, FileStateWriter

    from ._version_manifest import _MANIFEST_FILE

    manifest_path = target / ".trw" / _MANIFEST_FILE
    try:
        data = FileStateReader().read_yaml(manifest_path)
    except StateError:  # trw-fail-silent-allow: manifest_refusal already gated this call; unreachable in practice
        logger.warning("uninstall_manifest_rewrite_unreadable", path=str(manifest_path))
        return
    if not isinstance(data, dict):
        return
    content_hashes = data.get("content_hashes")
    if isinstance(content_hashes, dict):
        for key in removed_keys:
            content_hashes.pop(key, None)
    owners = data.get("owners")
    if isinstance(owners, dict):
        for key in removed_keys:
            owners.pop(key, None)
        for key, client_list in list(owners.items()):
            if isinstance(client_list, list) and remove_ide in client_list:
                owners[key] = [c for c in client_list if c != remove_ide]
    FileStateWriter().write_yaml(manifest_path, data)
