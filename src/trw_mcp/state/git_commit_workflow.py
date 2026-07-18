"""Verified production workflow for concurrency-isolated candidate commits.

Ownership is a two-step protocol. ``prepare_candidate_claim`` runs *before*
editing and persists the repository/run/path baseline. ``run_candidate_commit``
later loads that immutable claim, proves the same active TRW run journaled each
path after the claim, binds a successful post-claim build receipt, and only
then reviews and publishes the candidate. The publication path cannot mint
ownership from caller-selected current worktree bytes.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from typing_extensions import TypedDict

from trw_mcp.models.git_commit_transaction import OwnershipManifest
from trw_mcp.state._git_commit_workflow_provenance import (
    _binding_matches_final_content as _binding_matches_final_content,
)
from trw_mcp.state._git_commit_workflow_provenance import (
    _finalize_prepared_manifest as _finalize_prepared_manifest,
)
from trw_mcp.state._git_commit_workflow_provenance import (
    build_manifest_from_worktree as build_manifest_from_worktree,
)
from trw_mcp.state._git_commit_workflow_provenance import (
    prepare_candidate_claim as prepare_candidate_claim,
)
from trw_mcp.state.git_commit_transaction import (
    GitTransactionError,
    load_active_claims,
    publish_reviewed_candidate,
    record_candidate_review,
    release_claim,
    request_integration,
)

logger = structlog.get_logger(__name__)


class CandidateCommitResult(TypedDict):
    """Terminal result of one candidate-commit workflow run."""

    transaction_id: str
    candidate_ref: str
    candidate_oid: str
    reviewed_parent_oid: str
    owned_paths: list[str]
    handoff_digest: str
    preconditions: list[str]
    evidence_bound: bool
    integrated: bool


def run_candidate_commit(
    repo_root: Path,
    message: str,
    *,
    transaction_id: str,
    run_dir: Path,
    require_signature: bool = False,
    other_claims: tuple[OwnershipManifest, ...] = (),
) -> CandidateCommitResult:
    """Finalize a pre-edit claim, review, publish, and emit a handoff."""
    if not message.strip():
        raise GitTransactionError("commit message must not be empty")
    repo_root = repo_root.resolve()
    manifest = _finalize_prepared_manifest(repo_root, transaction_id, run_dir)
    concurrent_claims = other_claims + load_active_claims(repo_root, exclude_transaction_id=manifest.transaction_id)
    try:
        record_candidate_review(repo_root, manifest, message, other_claims=concurrent_claims)
        publish_reviewed_candidate(
            repo_root,
            manifest,
            message,
            require_signature=require_signature,
            other_claims=concurrent_claims,
        )
        journal, handoff = request_integration(repo_root, manifest.transaction_id)
    except GitTransactionError:
        release_claim(repo_root, manifest.transaction_id)
        raise
    if handoff is None:
        raise GitTransactionError("handoff unexpectedly absent after publication")

    from trw_mcp.state.candidate_evidence import record_candidate_evidence

    try:
        record_candidate_evidence(run_dir, journal)
    except OSError:
        logger.warning("candidate_evidence_binding_failed", transaction_id=journal.transaction_id, exc_info=True)
        evidence_bound = False
    else:
        evidence_bound = True
    return CandidateCommitResult(
        transaction_id=journal.transaction_id,
        candidate_ref=journal.candidate_ref,
        candidate_oid=journal.candidate_oid,
        reviewed_parent_oid=journal.parent_oid,
        owned_paths=list(handoff.owned_paths),
        handoff_digest=handoff.handoff_digest,
        preconditions=list(handoff.preconditions),
        evidence_bound=evidence_bound,
        integrated=False,
    )
