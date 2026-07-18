"""Verified ownership provenance for candidate-commit preparation."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from trw_mcp.models._evidence_core import ContentBinding, EntryState
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.git_commit_transaction import PREPARED_MANIFESTS_RELATIVE_DIR, OwnershipManifest
from trw_mcp.state._git_commit_claims import persist_claim
from trw_mcp.state.git_commit_transaction import GitTransactionError, _git, load_active_claims


def _digest_path(path: Path) -> str:
    """Return the raw-byte digest for a regular file, or absent marker."""
    if path.is_symlink():
        return "sha256:" + hashlib.sha256(os.readlink(path).encode("utf-8")).hexdigest()
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def _repository_identity(repo_root: Path) -> str:
    """Bind a claim to this checkout and repository history."""
    roots = _git(repo_root, "rev-list", "--max-parents=0", "HEAD").split()
    payload = {
        "worktree": str(repo_root.resolve()),
        "git_common_dir": str((repo_root / _git(repo_root, "rev-parse", "--git-common-dir").strip()).resolve()),
        "root_commits": sorted(roots),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalized_paths(repo_root: Path, paths: tuple[str, ...]) -> tuple[str, ...]:
    root = repo_root.resolve()
    normalized: list[str] = []
    for raw in paths:
        candidate = Path(raw)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise GitTransactionError(f"invalid owned path {raw!r}: traversal or absolute path")
        rel = candidate.as_posix().lstrip("./")
        if not rel or rel == ".":
            raise GitTransactionError("owned path must name a repository file")
        target = repo_root / rel
        if target.exists() and not target.resolve().is_relative_to(root):
            raise GitTransactionError(f"invalid owned path {raw!r}: escapes the repository root")
        normalized.append(rel)
    if not normalized or len(set(normalized)) != len(normalized):
        raise GitTransactionError("owned paths must be non-empty and unique")
    return tuple(sorted(normalized))


def _load_run_identity(repo_root: Path, run_dir: Path) -> str:
    """Verify a repository-local run directory with a live runtime pin."""
    root = repo_root.resolve()
    resolved_run = run_dir.resolve()
    try:
        resolved_run.relative_to(root / ".trw" / "runs")
    except ValueError as exc:
        raise GitTransactionError("run directory is not under this repository's .trw/runs") from exc
    run_file = resolved_run / "meta" / "run.yaml"
    try:
        payload = yaml.safe_load(run_file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise GitTransactionError("run metadata is missing or unreadable") from exc
    run_id = payload.get("run_id") if isinstance(payload, dict) else None
    if not isinstance(run_id, str) or not run_id:
        raise GitTransactionError("run metadata does not contain a valid run_id")
    if payload.get("status") != "active":
        raise GitTransactionError("run metadata is not active")
    pins_path = root / ".trw" / "runtime" / "pins.json"
    try:
        pins: object = json.loads(pins_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GitTransactionError("runtime pin store is missing or unreadable") from exc
    matching_pins = (
        [
            entry
            for entry in pins.values()
            if isinstance(entry, dict)
            and isinstance(entry.get("run_path"), str)
            and Path(entry["run_path"]).resolve() == resolved_run
        ]
        if isinstance(pins, dict)
        else []
    )
    if not matching_pins:
        raise GitTransactionError("run directory is not bound to an active runtime pin")
    now = datetime.now().astimezone()
    ttl_seconds = TRWConfig().pin_ttl_hours * 3600
    for entry in matching_pins:
        heartbeat = entry.get("last_heartbeat_ts")
        if not isinstance(heartbeat, str):
            continue
        try:
            heartbeat_at = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
        except ValueError:
            continue
        if abs((now - heartbeat_at.astimezone()).total_seconds()) <= ttl_seconds:
            break
    else:
        raise GitTransactionError("run directory's runtime pin is stale or malformed")
    return run_id


def _manifest_path(repo_root: Path, transaction_id: str) -> Path:
    return repo_root / PREPARED_MANIFESTS_RELATIVE_DIR / f"{transaction_id}.json"


def _persist_prepared_manifest(repo_root: Path, manifest: OwnershipManifest) -> Path:
    target = _manifest_path(repo_root, manifest.transaction_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise GitTransactionError(f"transaction id already prepared: {manifest.transaction_id}")
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest.model_dump(mode="json"), sort_keys=True, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(target)
    return target


def _load_prepared_manifest(repo_root: Path, transaction_id: str) -> OwnershipManifest:
    target = _manifest_path(repo_root, transaction_id)
    try:
        return OwnershipManifest.model_validate(json.loads(target.read_text(encoding="utf-8")), strict=False)
    except Exception as exc:
        raise GitTransactionError(f"prepared ownership manifest is missing or unreadable: {transaction_id}") from exc


def build_manifest_from_worktree(
    repo_root: Path,
    paths: tuple[str, ...],
    run_id: str,
    *,
    transaction_id: str = "",
) -> OwnershipManifest:
    """Build a low-level transaction fixture from current bytes.

    This helper exists for direct state-machine tests and recovery tooling. It
    is deliberately not used by either production CLI entrypoint and does not
    constitute verified ownership. Only ``prepare_candidate_claim`` followed
    by ``run_candidate_commit`` may publish through the production workflow.
    """
    repo_root = repo_root.resolve()
    owned_paths = _normalized_paths(repo_root, paths)
    current = {path: _digest_path(repo_root / path) for path in owned_paths}
    return OwnershipManifest(
        transaction_id=transaction_id or uuid.uuid4().hex[:16],
        run_id=run_id,
        repository_identity=_repository_identity(repo_root),
        checked_out_ref=_git(repo_root, "symbolic-ref", "-q", "HEAD").strip() or "(detached)",
        parent_oid=_git(repo_root, "rev-parse", "HEAD").strip(),
        owned_paths=owned_paths,
        pre_edit_digests=current,
        path_digests=current,
    )


def prepare_candidate_claim(
    repo_root: Path,
    paths: tuple[str, ...],
    run_dir: Path,
    *,
    transaction_id: str = "",
) -> OwnershipManifest:
    """Persist a verified ownership baseline before any owned path is edited."""
    repo_root = repo_root.resolve()
    run_id = _load_run_identity(repo_root, run_dir)
    owned_paths = _normalized_paths(repo_root, paths)
    txn = transaction_id or uuid.uuid4().hex[:16]
    if "/" in txn or "\\" in txn or txn in {".", ".."}:
        raise GitTransactionError("transaction id must be a single safe path component")
    concurrent = load_active_claims(repo_root, exclude_transaction_id=txn)
    foreign = {path for claim in concurrent for path in claim.owned_paths}
    overlap = sorted(set(owned_paths) & foreign)
    if overlap:
        raise GitTransactionError(f"ownership claim overlaps another transaction: {overlap[0]}")
    events_path = run_dir.resolve() / "meta" / "events.jsonl"
    event_offset = events_path.stat().st_size if events_path.exists() else 0
    pre_edit = {path: _digest_path(repo_root / path) for path in owned_paths}
    checked_out_ref = _git(repo_root, "symbolic-ref", "-q", "HEAD").strip() or "(detached)"
    manifest = OwnershipManifest(
        transaction_id=txn,
        run_id=run_id,
        repository_identity=_repository_identity(repo_root),
        checked_out_ref=checked_out_ref,
        parent_oid=_git(repo_root, "rev-parse", "HEAD").strip(),
        owned_paths=owned_paths,
        pre_edit_digests=pre_edit,
        path_digests=pre_edit,
        run_event_offset=event_offset,
        claimed_at=datetime.now().astimezone().isoformat(),
    )
    _persist_prepared_manifest(repo_root, manifest)
    persist_claim(repo_root, manifest)
    return manifest


def _post_claim_modified_paths(repo_root: Path, run_dir: Path, offset: int) -> set[str]:
    events_path = run_dir.resolve() / "meta" / "events.jsonl"
    try:
        with events_path.open("rb") as handle:
            handle.seek(offset)
            raw = handle.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GitTransactionError("run file-change journal is missing or unreadable") from exc
    paths: set[str] = set()
    for line in raw.splitlines():
        try:
            event: Any = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("event") == "file_modified" and isinstance(event.get("file"), str):
            raw_path = Path(event["file"])
            try:
                rel = raw_path.resolve().relative_to(repo_root) if raw_path.is_absolute() else raw_path
            except (OSError, ValueError):
                continue
            paths.add(rel.as_posix().lstrip("./"))
    return paths


def _binding_matches_final_content(
    binding: ContentBinding,
    repo_root: Path,
    final_digests: dict[str, str],
) -> bool:
    if binding.project_identity != repo_root.name:
        return False
    entries = {entry.path: entry for entry in binding.entries}
    for path, expected in final_digests.items():
        entry = entries.get(path)
        if entry is None:
            return False
        if expected == "":
            if entry.state is not EntryState.DELETED:
                return False
        elif entry.state is EntryState.FILE:
            target = repo_root / path
            if (
                entry.byte_digest != expected.removeprefix("sha256:")
                or not target.is_file()
                or entry.byte_size != target.stat().st_size
            ):
                return False
        elif entry.state is EntryState.SYMLINK:
            if entry.link_target is None:
                return False
            current = "sha256:" + hashlib.sha256(entry.link_target.encode("utf-8")).hexdigest()
            if current != expected:
                return False
        else:
            return False
    return True


def _successful_post_claim_build_receipts(
    repo_root: Path,
    run_dir: Path,
    manifest: OwnershipManifest,
    final_digests: dict[str, str],
) -> tuple[str, ...]:
    receipts: list[str] = []
    for path in sorted((run_dir.resolve() / "meta" / "receipts" / "build").glob("*.json")):
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
            completed = datetime.fromisoformat(str(payload["completed_at"]).replace("Z", "+00:00"))
            claimed = datetime.fromisoformat(manifest.claimed_at.replace("Z", "+00:00"))
            results = payload.get("command_results")
            successful_ids = (
                {
                    str(item.get("command_id"))
                    for item in results
                    if isinstance(item, dict) and item.get("exit_code") == 0
                }
                if isinstance(results, list)
                else set()
            )
            binding = ContentBinding.model_validate(payload.get("content_binding"), strict=False)
            passed = {"tests", "static_checks"}.issubset(successful_ids) and _binding_matches_final_content(
                binding, repo_root, final_digests
            )
            if payload.get("run_id") == manifest.run_id and completed >= claimed and passed:
                receipt_id = payload.get("receipt_id")
                if isinstance(receipt_id, str) and receipt_id.startswith("build-"):
                    receipts.append(receipt_id)
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            continue
    if not receipts:
        raise GitTransactionError("no successful post-claim build receipt is bound to the owning run")
    return tuple(receipts)


def _finalize_prepared_manifest(repo_root: Path, transaction_id: str, run_dir: Path) -> OwnershipManifest:
    manifest = _load_prepared_manifest(repo_root, transaction_id)
    run_id = _load_run_identity(repo_root, run_dir)
    if run_id != manifest.run_id:
        raise GitTransactionError("prepared manifest belongs to a different TRW run")
    if _repository_identity(repo_root) != manifest.repository_identity:
        raise GitTransactionError("prepared manifest belongs to a different repository")
    checked_out_ref = _git(repo_root, "symbolic-ref", "-q", "HEAD").strip() or "(detached)"
    if checked_out_ref != manifest.checked_out_ref:
        raise GitTransactionError("checked-out branch changed after ownership preparation")
    if _git(repo_root, "rev-parse", "HEAD").strip() != manifest.parent_oid:
        raise GitTransactionError("repository parent changed after ownership preparation")
    modified = _post_claim_modified_paths(repo_root, run_dir, manifest.run_event_offset)
    missing_events = sorted(set(manifest.owned_paths) - modified)
    if missing_events:
        raise GitTransactionError(f"owned path lacks a post-claim run journal event: {missing_events[0]}")
    final_digests = {path: _digest_path(repo_root / path) for path in manifest.owned_paths}
    unchanged = sorted(
        path for path in manifest.owned_paths if final_digests[path] == manifest.pre_edit_digests.get(path)
    )
    if unchanged:
        raise GitTransactionError(f"owned path did not change after preparation: {unchanged[0]}")
    receipts = _successful_post_claim_build_receipts(repo_root, run_dir, manifest, final_digests)
    return manifest.model_copy(update={"path_digests": final_digests, "evidence_receipt_ids": receipts})
