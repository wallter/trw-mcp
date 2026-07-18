"""Shared-state snapshots and ownership claims for Git transactions."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path

from trw_mcp.models.git_commit_transaction import (
    OwnershipManifest,
    SharedStateSnapshot,
    TransactionJournal,
    TransactionState,
)

CLAIMS_RELATIVE_DIR = ".trw/git-transactions/claims"
_CLAIM_RELEASED_STATES = (
    TransactionState.FAILED.value,
    TransactionState.HANDOFF_READY.value,
    TransactionState.CANDIDATE_ONLY.value,
)


def snapshot_shared_state(repo_root: Path, *, git: Callable[..., str]) -> SharedStateSnapshot:
    """Capture the semantic shared-checkout state."""
    checked_out = git(repo_root, "symbolic-ref", "-q", "HEAD").strip() or "(detached)"
    head_oid = git(repo_root, "rev-parse", "HEAD").strip()
    index_path = repo_root / ".git" / "index"
    index_digest = "sha256:" + hashlib.sha256(index_path.read_bytes()).hexdigest() if index_path.exists() else "absent"
    porcelain = git(repo_root, "--no-optional-locks", "status", "--porcelain=v2", "--untracked-files=no")
    return SharedStateSnapshot(
        checked_out_ref=checked_out,
        head_oid=head_oid,
        shared_index_digest=index_digest,
        porcelain_status=porcelain,
    )


def validate_ownership(
    manifest: OwnershipManifest,
    repo_root: Path,
    *,
    other_claims: tuple[OwnershipManifest, ...] = (),
) -> list[str]:
    """Validate a content-bound ownership claim without mutation."""
    failures: list[str] = []
    resolved_root = repo_root.resolve()
    foreign_owned = {path for claim in other_claims for path in claim.owned_paths}
    for path in manifest.owned_paths:
        candidate = Path(path)
        if candidate.is_absolute() or ".." in candidate.parts:
            failures.append(f"{path}: traversal or absolute path")
            continue
        target = repo_root / candidate
        if target.exists() and not target.resolve().is_relative_to(resolved_root):
            failures.append(f"{path}: escapes the repository root")
            continue
        if path in foreign_owned:
            failures.append(f"{path}: overlaps another transaction's ownership claim")
            continue
        claimed = manifest.path_digests.get(path)
        if claimed is None:
            failures.append(f"{path}: unowned — no content binding in the manifest")
        elif claimed == "":
            if target.exists():
                failures.append(f"{path}: declared absent but exists")
        elif not target.exists() and not target.is_symlink():
            failures.append(f"{path}: changed since claim (missing)")
        else:
            raw = os.readlink(target).encode("utf-8") if target.is_symlink() else target.read_bytes()
            current = "sha256:" + hashlib.sha256(raw).hexdigest()
            if current != claimed:
                failures.append(f"{path}: changed since claim (content digest mismatch)")
    return failures


def persist_claim(repo_root: Path, manifest: OwnershipManifest) -> Path:
    """Persist an ownership claim atomically."""
    claims_dir = repo_root / CLAIMS_RELATIVE_DIR
    claims_dir.mkdir(parents=True, exist_ok=True)
    target = claims_dir / f"{manifest.transaction_id}.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest.model_dump(mode="json"), sort_keys=True, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(target)
    return target


def release_claim(repo_root: Path, transaction_id: str) -> None:
    """Remove a transaction's ownership claim."""
    (repo_root / CLAIMS_RELATIVE_DIR / f"{transaction_id}.json").unlink(missing_ok=True)


def load_active_claims(
    repo_root: Path,
    *,
    exclude_transaction_id: str = "",
    read_journal: Callable[[Path, str], TransactionJournal | None],
    error_type: type[RuntimeError],
) -> tuple[OwnershipManifest, ...]:
    """Load all other transactions' live claims, failing closed."""
    claims_dir = repo_root / CLAIMS_RELATIVE_DIR
    if not claims_dir.is_dir():
        return ()
    claims: list[OwnershipManifest] = []
    for claim_file in sorted(claims_dir.glob("*.json")):
        if claim_file.stem == exclude_transaction_id:
            continue
        try:
            claim = OwnershipManifest.model_validate(json.loads(claim_file.read_text(encoding="utf-8")), strict=False)
        except Exception as exc:
            raise error_type(f"unreadable ownership claim {claim_file.name} — resolve before committing") from exc
        journal = read_journal(repo_root, claim.transaction_id)
        if journal is None or (not journal.tombstoned and str(journal.state) not in _CLAIM_RELEASED_STATES):
            claims.append(claim)
    return tuple(claims)
