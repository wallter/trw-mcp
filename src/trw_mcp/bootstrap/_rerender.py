"""Selective update controls and durable, race-checked replacement of managed files.

Rerenders run the existing update pipeline in scratch, then publish only requested
surfaces. Displaced bytes remain in timestamped .trw/trash captures, never in the
successful transaction's disposable snapshot. No global effects run in scratch.
"""

from __future__ import annotations

import hashlib
import io
import os
import stat
from pathlib import Path

from ruamel.yaml import YAML

from trw_mcp._checkout_access import open_under
from trw_mcp._checkout_write import record_run_write, recording_writes, write_checkout_file

from ._proven_replace import Replaced


def read_optional(root: Path, rel: str) -> bytes | None:
    """Read a regular file without following links; only ENOENT means absent."""
    payload = None
    try:
        fd = open_under(root, rel)
    except FileNotFoundError:
        fd = None
    if fd is not None:
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise OSError(f"{rel}: not a regular file")
            payload = stream.read()
    return payload


def _create_render(path: Path, root: Path, new: bytes, mode: int) -> Replaced:
    """Publish a new render with its executable bits, using the shared exclusive staging protocol."""
    from ._proven_replace import _NEW, _staged, _Unstageable

    try:
        with _staged(root, path.relative_to(root).parts, new, mode) as (parent, staged):
            os.link(_NEW, path.name, src_dir_fd=staged, dst_dir_fd=parent, follow_symlinks=False)
    except (OSError, _Unstageable) as exc:
        return Replaced("refused", None, f"render could not be published: {exc}", failed=True)
    return Replaced("replaced", None, "")


def publish_with_backup(
    root: Path,
    rel: str,
    old: bytes | None,
    new: bytes,
    result: dict[str, list[str]],
    *,
    mode: int | None = None,
    backups: list[Path] | None = None,
) -> bool:
    """Publish only against the observed bytes, retaining and reporting any displaced version."""
    from ._proven_replace import create_exclusive, replace_proven

    path = root / rel
    if old is None:
        from trw_memory.safe_fs import _open_parent

        os.close(_open_parent(root, Path(rel).parts[:-1]))
    if old is None:
        outcome = create_exclusive(path, root, new) if mode is None else _create_render(path, root, new, mode)
    else:
        outcome = replace_proven(path, root, old, new, mode=mode)
    if outcome.previous is not None:
        result.setdefault("warnings", []).append(f"{rel}: backup kept at {outcome.previous}")
    if outcome.status != "replaced":
        result.setdefault("errors", []).append(f"{rel}: write refused: {outcome.reason}")
        return False
    if backups is not None and outcome.previous is not None:
        backups.append(outcome.previous)
    record_run_write(path, new)
    return True


def validate_update_target(root: Path, result: dict[str, list[str]]) -> bool:
    """Refuse wrong directories or untrustworthy manifests before any writer runs."""
    from ._utils import is_git_repo
    from ._version_manifest import manifest_refusal

    # init-project installs outside git (PRD-INFRA-170-FR06), so the update that adds a second client to that same
    # project must run there too: only a linked ``.git`` is refused, and a missing one is a warning naming the remedy.
    if (root / ".git").is_symlink():
        result["errors"].append(f"{root} is not a git repository (.git is a symbolic link)")
    elif not (root / ".trw").exists():
        result["errors"].append(
            f"{root} does not have TRW installed (.trw/ not found). Run `trw-mcp init-project` first."
        )
    elif refusal := manifest_refusal(root):
        result["errors"].append(refusal)
    elif not is_git_repo(root):
        result.setdefault("warnings", []).append(
            f"{root} is not a git repository — updating anyway; run 'git init' to enable git-based features."
        )
    return not result["errors"]


def _requested_paths(root: Path, paths: list[str]) -> dict[str, bytes | None]:
    from trw_mcp.client_profiles.catalog import client_surfaces, uninstall_surfaces
    from trw_mcp.models.config._profiles import builtin_client_ids

    from ._manifest_recorders import under_recorder_surface
    from ._safe_remove import path_refusal
    from ._template_updater import _ALWAYS_UPDATE, _NEVER_OVERWRITE

    shared = {s.relpath for s in uninstall_surfaces() if s.merged_config or s.managed_block}
    surfaces = {s.relpath for c in builtin_client_ids() for s in client_surfaces(c) if not s.home_scoped}
    surfaces.update(rel for _, rel in _ALWAYS_UPDATE)
    selected: dict[str, bytes | None] = {}
    for rel in paths:
        if not rel or Path(rel).is_absolute() or any(p in ("", ".", "..") for p in rel.split("/")):
            raise ValueError(f"--rerender requires a repo-relative file path: {rel!r}")
        if rel in shared:
            raise ValueError(f"--rerender {rel!r}: shared surface; use plain update-project to merge it")
        if rel in _NEVER_OVERWRITE or not (rel in surfaces or under_recorder_surface(rel)):
            raise ValueError(f"--rerender {rel!r} is not a TRW-managed surface")
        if refusal := path_refusal(root / rel, root):
            raise ValueError(f"--rerender {rel!r}: {refusal}")
        selected[rel] = read_optional(root, rel)
    return selected


def rerender_project(
    root: Path, paths: list[str], data: Path, ide: str | None, result: dict[str, list[str]], *, dry_run: bool
) -> dict[str, list[str]]:
    """Render requested paths in isolation; validate every render before publishing any."""
    from ._update_project import _apply_update, _init_result_dict
    from ._update_transaction import run_in_scratch
    from ._version_manifest import _manifest_key_path, _read_manifest

    try:
        selected = _requested_paths(root, paths)
    except (OSError, ValueError) as exc:
        result["errors"].append(str(exc))
        return result
    candidates: dict[str, tuple[bytes, int]] = {}
    preview = _init_result_dict(True)

    def render(scratch: Path) -> None:
        manifest = _read_manifest(scratch)
        if manifest is None:
            raise ValueError("rerender scratch manifest is missing")
        for field in ("content_hashes", "tombstones"):
            entries = manifest.get(field)
            if isinstance(entries, dict):
                manifest[field] = {k: v for k, v in entries.items() if _manifest_key_path(str(k)) not in selected}
            elif isinstance(entries, list):
                manifest[field] = [k for k in entries if _manifest_key_path(str(k)) not in selected]
        stream = io.StringIO()
        YAML(typ="safe").dump(manifest, stream)
        write_checkout_file(scratch, scratch / ".trw/managed-artifacts.yaml", stream.getvalue())
        for rel in selected:
            (scratch / rel).unlink(missing_ok=True)
        _apply_update(scratch, data, preview, ide=ide, on_progress=None, dirty=None, reprovision=None, dry_run=True)
        if preview["errors"]:
            return
        for rel in selected:
            rendered = read_optional(scratch, rel)
            if rendered is None:
                preview["errors"].append(f"--rerender {rel!r}: no current TRW render for this project's clients")
            else:
                candidates[rel] = rendered, stat.S_IMODE((scratch / rel).stat().st_mode)

    try:
        run_in_scratch(root, preview, render)
        result["errors"].extend(preview["errors"])
        if not result["errors"]:
            for rel, (rendered, mode) in candidates.items():
                if result["errors"]:
                    break
                old = selected[rel]
                if old == rendered and stat.S_IMODE((root / rel).stat().st_mode) == mode:
                    result["preserved"].append(rel)
                elif dry_run or _publish_rerender(root, rel, old, rendered, mode, result):
                    result["created" if old is None else "updated"].append(rel)
                    if dry_run and old is not None:
                        result["warnings"].append(f"{rel}: would back up old bytes under {root / '.trw/trash'}")
    except (OSError, ValueError) as exc:
        result["errors"].append(f"rerender failed: {exc}")
    return result


def _publish_rerender(
    root: Path, rel: str, old: bytes | None, rendered: bytes, mode: int, result: dict[str, list[str]]
) -> bool:
    """Adopt only a successfully published render as the next update's baseline."""
    from ._version_manifest import _manifest_key_for, _manifest_key_path, _read_manifest
    from ._written_digests import record_written_digests

    manifest = _read_manifest(root)
    if manifest is None:
        raise ValueError("rerender manifest is missing")
    with recording_writes():
        if not publish_with_backup(root, rel, old, rendered, result, mode=mode):
            return False
        hashes = manifest.get("content_hashes")
        manifest["content_hashes"] = {
            **(hashes if isinstance(hashes, dict) else {}),
            _manifest_key_for(rel): hashlib.sha256(rendered).hexdigest(),
        }
        tombstones = manifest.get("tombstones")
        if isinstance(tombstones, list):
            manifest["tombstones"] = [k for k in tombstones if _manifest_key_path(str(k)) != rel]
        stream = io.StringIO()
        YAML(typ="safe").dump(manifest, stream)
        write_checkout_file(root, root / ".trw/managed-artifacts.yaml", stream.getvalue())
        record_written_digests(root, result)
    return True


def preserve_uncommitted_changes(
    target_dir: Path,
    snapshot_root: Path,
    dirty: set[str],
    manifest_hashes: dict[str, str] | None,
    result: dict[str, list[str]],
) -> None:
    """Undo every write to a dirty path whose pre-run bytes TRW did not record.

    Runs after the writers and before the manifest is recorded, so the
    ownership recorders see the preserved bytes. A dirty path whose pre-run
    bytes hash to its ``content_hashes`` record is TRW's own last write and
    keeps the refresh.

    ``.trw/INSTRUCTIONS.md`` is skipped: its writer refuses a user-authored
    file and backs up a generated one before replacing it (PRD-CORE-341-FR07),
    so undoing its refresh protected nothing and re-created the stale file on
    every run (the retire loop, FR08).
    """
    import hashlib

    from trw_mcp.state.claude_md._instructions_link import INSTRUCTIONS_RELPATH

    from ._canon_ownership import is_trw_deployed_canon, is_trw_owned_runtime_canon
    from ._dirty_refresh import refresh_loses_nothing
    from ._enrollment_rebless import marker_rel, rebless_loses_nothing
    from ._update_transaction import _file_signature, _is_under_pruned_dir, _restore_transaction_file
    from ._version_manifest import _manifest_key_for

    # A path retired in place this run was proven TRW's, or is committed in git: restoring it would
    # re-deploy a withdrawn hook on every update.
    retired = set(result.get("retired", []))
    for rel in sorted(dirty):
        if _is_under_pruned_dir(target_dir, rel):  # never snapshotted, never written: not ours to inspect
            continue
        if rel in retired:
            continue
        before, after = snapshot_root / rel, target_dir / rel
        # A pre-run absence has no user bytes to restore; tombstone enforcement runs later.
        if not before.is_symlink() and read_optional(snapshot_root, rel) is None:
            continue
        if rel == INSTRUCTIONS_RELPATH or _file_signature(before) == _file_signature(after):
            continue
        if is_trw_deployed_canon(snapshot_root, rel):  # the canon's receipt, not content_hashes, records it
            continue
        if is_trw_owned_runtime_canon(rel):  # TRW-owned: a hand edit is drift the redeploy repairs
            continue
        if (
            before.is_file()
            and after.is_file()
            and not (before.is_symlink() or after.is_symlink())
            and before.read_bytes() == after.read_bytes()
        ):
            continue  # same bytes, different mode: a restored exec bit changes no content of the user's
        if refresh_loses_nothing(rel, before, after, root=target_dir):
            continue  # TRW's own last write, .mcp.json's `trw` entry, or AGENTS.md's TRW block only (_dirty_refresh)
        if before.is_file() and not before.is_symlink():
            recorded = (manifest_hashes or {}).get(_manifest_key_for(rel))
            if recorded == hashlib.sha256(before.read_bytes()).hexdigest():
                continue
        if rel == marker_rel() and rebless_loses_nothing(target_dir, before, after):
            continue  # only the hook digest moved, over the bundled hooks: the user's other keys are intact
        _restore_transaction_file(target_dir, snapshot_root, rel, result.setdefault("warnings", []))
        result.setdefault("preserved", []).append(f"{rel} (uncommitted_changes)")


def resolve_client_write_targets(target_dir: Path, ide_override: str | None = None) -> list[str]:
    """The clients an update should WRITE artifacts for.

    One authority, because the record and raw detection disagree. Detection
    reports claude-code for any project containing ``.claude/`` — which TRW
    creates for EVERY client, since hooks and skills are universal artifacts —
    so a bare update on a Codex project would otherwise scaffold Claude Code's
    surfaces from TRW's own scaffolding. The recorded client list is honoured
    when the caller names no override; detection answers only where there is no
    record (a pre-record install).

    Extracted so the agent update path (``_template_updater._update_agents``)
    and the client-integration update path (``_update_project``) cannot
    disagree about which clients an update is for — install and update
    diverging over exactly this is what PRD-CORE-252-FR03 routes through one
    function.
    """
    from ._utils import resolve_ide_targets

    if not ide_override:
        from ._template_claude_md import _recorded_targets

        recorded = _recorded_targets(target_dir)
        if recorded:
            return list(recorded)
    return resolve_ide_targets(target_dir, ide_override=ide_override)
