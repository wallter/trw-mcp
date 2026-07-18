"""Crash-safe, shared-state-preserving Git commit transaction (PRD-CORE-219).

Sole implementation of the candidate-commit state machine. Every operation:

- builds candidates in a SESSION-OWNED alternate index (``GIT_INDEX_FILE``)
  rooted at the reviewed parent — the shared index file is never read for
  content and never written;
- publishes only to the namespaced candidate ref via compare-and-swap
  (``git update-ref`` with an expected old value) — the checked-out branch,
  HEAD, worktree bytes, and porcelain status are semantically unchanged
  (snapshot-verified before/after, NFR01);
- journals every state transition atomically (NFR03);
- REFUSES automatic integration: any request returns ``candidate_only`` with
  ``automatic_integration_unsupported`` and no shared-state mutation (FR06).

Subprocess arguments are fixed lists; paths are repository-relative (NFR04).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

import structlog

from trw_mcp.models.git_commit_transaction import (
    JOURNALS_RELATIVE_DIR,
    REASON_AUTO_INTEGRATION_UNSUPPORTED,
    NativeIntegrationHandoff,
    OwnershipManifest,
    SharedStateSnapshot,
    TransactionJournal,
    TransactionState,
)
from trw_mcp.state._git_commit_claims import (
    load_active_claims as _load_active_claims_impl,
)
from trw_mcp.state._git_commit_claims import (
    persist_claim,
    validate_ownership,
)
from trw_mcp.state._git_commit_claims import (
    release_claim as release_claim,
)
from trw_mcp.state._git_commit_claims import (
    snapshot_shared_state as _snapshot_shared_state_impl,
)
from trw_mcp.state._git_commit_hooks import run_blocking_hooks as _run_blocking_hooks_impl

logger = structlog.get_logger(__name__)


class GitTransactionError(RuntimeError):
    """Typed transaction failure — shared state is guaranteed untouched."""


def _git(repo_root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    """Run a fixed-argument git command; typed failure on nonzero exit."""
    merged_env = {**os.environ, **(env or {})}
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo_root), *args],  # noqa: S607
        capture_output=True,
        text=True,
        env=merged_env,
    )
    if result.returncode != 0:
        raise GitTransactionError(f"git {args[0]} failed: {result.stderr.strip()[:300]}")
    return result.stdout


# ---------------------------------------------------------------------------
# FR01 — ownership validation (read-only)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Cross-transaction claim registry (FR01 — production overlap detection)
# ---------------------------------------------------------------------------


# TOCTOU window (PRD-CORE-219 round-2 audit, accepted): this is a LOCK-FREE
# advisory registry. Between one transaction's ``load_active_claims`` read and
# its ``persist_claim`` write, a second transaction can pass the same overlap
# check, so two simultaneous claims over overlapping paths can both persist.
# The blast radius is bounded to two immutable candidate refs — NEVER two
# integrations: candidate publication only writes an isolated ``refs/trw/...``
# ref, and native integration happens LATER under repository quiescence, where
# the loser's candidate is re-validated against then-current HEAD and refused.
# An O_EXCL per-path claim lock would close the window but adds cross-platform
# locking complexity for a race not observed in practice; deferred until real
# collisions are seen. See docs/documentation/improvement-backlog.md.
#
# Claim files bind only while a transaction is IN FLIGHT (building toward a
# candidate). Handoff transfers ownership to the later quiesced integrator,
# and failure abandons it — either way the paths unblock for other agents.


def snapshot_shared_state(repo_root: Path) -> SharedStateSnapshot:
    """Capture the semantic shared-checkout state (NFR01 no-op oracle)."""
    return _snapshot_shared_state_impl(repo_root, git=_git)


def load_active_claims(repo_root: Path, *, exclude_transaction_id: str = "") -> tuple[OwnershipManifest, ...]:
    """Load all other live claims, failing closed on unreadable state."""
    return _load_active_claims_impl(
        repo_root,
        exclude_transaction_id=exclude_transaction_id,
        read_journal=read_journal,
        error_type=GitTransactionError,
    )


# ---------------------------------------------------------------------------
# Journal (NFR03)
# ---------------------------------------------------------------------------


def _advance(journal: TransactionJournal, state: TransactionState, **updates: str) -> TransactionJournal:
    """Rebuild the journal through validation so enum values persist as wire strings."""
    return TransactionJournal.model_validate(
        {**journal.model_dump(mode="json"), "state": state.value, **updates}, strict=False
    )


def _journal_path(repo_root: Path, transaction_id: str) -> Path:
    return repo_root / JOURNALS_RELATIVE_DIR / f"{transaction_id}.json"


def write_journal(repo_root: Path, journal: TransactionJournal) -> Path:
    target = _journal_path(repo_root, journal.transaction_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".json.tmp")
    tmp = Path(tmp_name)
    try:
        os.close(fd)
        tmp.write_text(json.dumps(journal.model_dump(mode="json"), sort_keys=True, indent=2) + "\n")
        os.chmod(tmp, 0o600)  # NFR04: restrictive permissions
        tmp.replace(target)
    except Exception:  # justified: cleanup must not mask the original error
        tmp.unlink(missing_ok=True)
        raise
    return target


def read_journal(repo_root: Path, transaction_id: str) -> TransactionJournal | None:
    target = _journal_path(repo_root, transaction_id)
    if not target.exists():
        return None
    try:
        return TransactionJournal.model_validate(json.loads(target.read_text(encoding="utf-8")), strict=False)
    except Exception:  # justified: unreadable journal is typed absence for recovery
        logger.warning("git_transaction_journal_unreadable", transaction_id=transaction_id, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# FR02/FR05 — isolated candidate construction + CAS publication
# ---------------------------------------------------------------------------


def build_and_publish_candidate(
    manifest: OwnershipManifest,
    repo_root: Path,
    message: str,
    *,
    other_claims: tuple[OwnershipManifest, ...] = (),
    sign: bool = False,
) -> TransactionJournal:
    """Build the candidate commit in an isolated index and CAS-publish its ref.

    The candidate tree = the reviewed parent's tree with ONLY the owned paths
    updated to their claimed content (a strict subset delta, NFR02). Shared
    state is snapshot-verified unchanged; any drift is a typed failure AFTER
    the fact (the helper itself performs no shared-state writes).
    """
    failures = validate_ownership(manifest, repo_root, other_claims=other_claims)
    if failures:
        raise GitTransactionError("ownership validation failed: " + "; ".join(failures[:5]))

    before = snapshot_shared_state(repo_root)
    journal = TransactionJournal(
        transaction_id=manifest.transaction_id,
        state=TransactionState.PREPARED,
        manifest_digest=manifest.canonical_digest(),
        parent_oid=manifest.parent_oid,
        checked_out_ref=before.checked_out_ref,
        shared_index_digest=before.shared_index_digest,
    )
    write_journal(repo_root, journal)

    with tempfile.TemporaryDirectory(prefix="trw-txn-") as tmp_dir:
        private_index = Path(tmp_dir) / "index"
        os.makedirs(tmp_dir, exist_ok=True)
        env = {"GIT_INDEX_FILE": str(private_index)}
        # Root the private index at the reviewed parent tree.
        _git(repo_root, "read-tree", manifest.parent_oid, env=env)
        for path in manifest.owned_paths:
            if manifest.path_digests.get(path) == "":
                _git(repo_root, "update-index", "--force-remove", "--", path, env=env)
                continue
            # Let Git derive the complete cache entry in the parent-rooted
            # private index. Unlike a hard-coded cacheinfo mode, this preserves
            # Git's regular/executable and symlink semantics (100644, 100755,
            # 120000) without consulting or mutating the shared index.
            _git(repo_root, "add", "--force", "--", path, env=env)
        tree_oid = _git(repo_root, "write-tree", env=env).strip()
        commit_args = ["commit-tree", tree_oid, "-p", manifest.parent_oid, "-m", message]
        if sign:
            commit_args.insert(1, "-S")  # real GPG signing at creation (FR04)
        try:
            candidate_oid = _git(repo_root, *commit_args).strip()
        except GitTransactionError as exc:
            if sign:
                failed = _advance(journal, TransactionState.FAILED, reason="signing_failed")
                write_journal(repo_root, failed)
                raise GitTransactionError(
                    "publication blocked: candidate signing failed — required signature "
                    "did not verify (no usable signing key?)"
                ) from exc
            raise

    candidate_ref = manifest.candidate_ref()
    # Compare-and-swap from absent (or the recorded prior candidate) only.
    _git(repo_root, "update-ref", candidate_ref, candidate_oid, "0" * 40)

    after = snapshot_shared_state(repo_root)
    if after != before:
        journal = _advance(journal, TransactionState.FAILED, reason="shared_state_drift_detected")
        write_journal(repo_root, journal)
        raise GitTransactionError("shared checkout state changed during candidate construction")

    journal = _advance(
        journal,
        TransactionState.CANDIDATE_PUBLISHED,
        candidate_oid=candidate_oid,
        candidate_ref=candidate_ref,
    )
    write_journal(repo_root, journal)
    logger.info("git_candidate_published", ref=candidate_ref, oid=candidate_oid[:12])
    return journal


# ---------------------------------------------------------------------------
# FR06 — candidate-only native-integration handoff
# ---------------------------------------------------------------------------


def request_integration(
    repo_root: Path,
    transaction_id: str,
    *,
    automatic: bool = False,
) -> tuple[TransactionJournal, NativeIntegrationHandoff | None]:
    """Emit the content-bound handoff; REFUSE any automatic integration.

    ``automatic=True`` (or any future flag combination) returns
    ``candidate_only`` with ``automatic_integration_unsupported`` and performs
    zero shared-state writes — a framework-private lease cannot exclude
    ordinary Git writers, so checked-out-branch integration is permanently a
    native-Git, quiesced, human-supervised operation.
    """
    journal = read_journal(repo_root, transaction_id)
    if journal is None or not journal.candidate_oid:
        raise GitTransactionError(f"no published candidate for transaction {transaction_id}")

    if automatic:
        refused = _advance(journal, TransactionState.CANDIDATE_ONLY, reason=REASON_AUTO_INTEGRATION_UNSUPPORTED)
        write_journal(repo_root, refused)
        return refused, None

    # FR06 parent-race enforcement: the handoff is valid ONLY while the
    # reviewed parent is still the tip of the recorded checked-out ref. A
    # drifted parent makes the candidate STALE — refuse and require a fresh
    # candidate + review; the precondition is checked here, not just prose.
    if journal.checked_out_ref and journal.checked_out_ref != "(detached)":
        try:
            current_tip = _git(repo_root, "rev-parse", journal.checked_out_ref).strip()
        except GitTransactionError as exc:
            # FR06 fail-CLOSED: if we cannot resolve the current tip we cannot
            # prove the reviewed parent is still current, so refuse the handoff
            # rather than coercing to "" (which would silently skip the check).
            # release-verify 2026-07-17 P1.
            stale = _advance(journal, TransactionState.FAILED, reason="parent_ref_unresolvable")
            write_journal(repo_root, stale)
            raise GitTransactionError(
                "handoff blocked: cannot resolve the reviewed branch tip "
                f"({journal.checked_out_ref}) to verify it has not advanced — "
                "rebuild and re-review the candidate"
            ) from exc
        if current_tip and current_tip != journal.parent_oid:
            stale = _advance(journal, TransactionState.FAILED, reason="stale_parent_requires_re_review")
            write_journal(repo_root, stale)
            raise GitTransactionError(
                "handoff blocked: the target branch advanced past the reviewed parent — "
                "rebuild and re-review the candidate"
            )

    manifest_paths = _git(repo_root, "diff-tree", "--no-commit-id", "--name-only", "-r", journal.candidate_oid).split()
    handoff = NativeIntegrationHandoff(
        transaction_id=transaction_id,
        candidate_ref=journal.candidate_ref,
        candidate_oid=journal.candidate_oid,
        reviewed_parent_oid=journal.parent_oid,
        owned_paths=tuple(manifest_paths),
        handoff_digest="sha256:"
        + hashlib.sha256(
            json.dumps([journal.candidate_oid, journal.parent_oid, sorted(manifest_paths)], sort_keys=True).encode(
                "utf-8"
            )
        ).hexdigest(),
    )
    ready = _advance(journal, TransactionState.HANDOFF_READY)
    write_journal(repo_root, ready)
    # Ownership transfers to the (later, quiesced) native integrator — the
    # in-flight claim no longer blocks other agents' transactions.
    release_claim(repo_root, transaction_id)
    return ready, handoff


# ---------------------------------------------------------------------------
# FR03 — immutable candidate-diff review binding
# ---------------------------------------------------------------------------


def record_candidate_review(
    repo_root: Path,
    manifest: OwnershipManifest,
    message: str,
    *,
    other_claims: tuple[OwnershipManifest, ...] = (),
) -> TransactionJournal:
    """Bind the review to the exact candidate inputs (FR03).

    The review covers the ownership manifest digest, the reviewed parent, and
    the commit message. Publication later revalidates ALL of these — an
    unexpected path, a post-review content edit, a parent change, or a message
    change blocks publication until a NEW review is recorded. Overlap with
    ``other_claims`` (concurrent transactions' persisted ownership) refuses
    the review, and a successful review PERSISTS this claim so concurrent
    transactions see it (FR01).
    """
    existing = read_journal(repo_root, manifest.transaction_id)
    if existing is not None and existing.tombstoned:
        raise GitTransactionError("review refused: transaction id is tombstoned (reuse rejected)")
    failures = validate_ownership(manifest, repo_root, other_claims=other_claims)
    if failures:
        raise GitTransactionError("review refused — ownership invalid: " + "; ".join(failures[:5]))
    persist_claim(repo_root, manifest)
    message_digest = "sha256:" + hashlib.sha256(message.encode("utf-8")).hexdigest()
    journal = TransactionJournal(
        transaction_id=manifest.transaction_id,
        state=TransactionState.REVIEWED,
        manifest_digest=manifest.canonical_digest(),
        parent_oid=manifest.parent_oid,
        reason=f"review_bound:{message_digest}",
    )
    write_journal(repo_root, journal)
    return journal


def publish_reviewed_candidate(
    repo_root: Path,
    manifest: OwnershipManifest,
    message: str,
    *,
    require_signature: bool = False,
    other_claims: tuple[OwnershipManifest, ...] = (),
) -> TransactionJournal:
    """Publish ONLY a currently-reviewed, unchanged candidate (FR03/FR04).

    Blocks (typed failure, no shared-state effect) when: no review journal
    exists; the manifest digest, parent, or message changed since review; the
    transaction is tombstoned; blocking hooks fail or mutate the candidate
    context; or a required signature does not verify.
    """
    journal = read_journal(repo_root, manifest.transaction_id)
    if journal is None or str(journal.state) != TransactionState.REVIEWED.value:
        raise GitTransactionError("publication blocked: no current review for this candidate")
    if journal.tombstoned:
        raise GitTransactionError("publication blocked: transaction id is tombstoned (reuse rejected)")
    if journal.manifest_digest != manifest.canonical_digest():
        raise GitTransactionError("publication blocked: candidate content changed since review")
    if journal.parent_oid != manifest.parent_oid:
        raise GitTransactionError("publication blocked: reviewed parent changed")
    message_digest = "sha256:" + hashlib.sha256(message.encode("utf-8")).hexdigest()
    if journal.reason != f"review_bound:{message_digest}":
        raise GitTransactionError("publication blocked: commit message changed since review")

    _run_blocking_hooks(repo_root, manifest, message)
    published = build_and_publish_candidate(
        manifest, repo_root, message, other_claims=other_claims, sign=require_signature
    )
    if require_signature:
        try:
            _git(repo_root, "verify-commit", published.candidate_oid)
        except GitTransactionError as exc:
            failed = _advance(published, TransactionState.FAILED, reason="signature_verification_failed")
            write_journal(repo_root, failed)
            raise GitTransactionError("publication blocked: required signature did not verify") from exc
    return published


# ---------------------------------------------------------------------------
# FR04 — exact blocking-hook contract (isolated candidate context)
# ---------------------------------------------------------------------------


def _mark_hook_mutation_for_review(repo_root: Path, transaction_id: str) -> None:
    """Invalidate the review after a hook changes its isolated candidate."""
    journal = read_journal(repo_root, transaction_id)
    if journal is not None:
        write_journal(
            repo_root,
            _advance(journal, TransactionState.PREPARED, reason="hook_mutation_invalidated_review"),
        )


def _run_blocking_hooks(repo_root: Path, manifest: OwnershipManifest, message: str) -> None:
    """Delegate native blocking hooks to the isolated candidate context."""
    _run_blocking_hooks_impl(
        repo_root,
        manifest,
        message,
        git=_git,
        mark_mutation=_mark_hook_mutation_for_review,
        error_type=GitTransactionError,
    )


# ---------------------------------------------------------------------------
# NFR03 — crash recovery
# ---------------------------------------------------------------------------


def recover_transaction(repo_root: Path, transaction_id: str) -> TransactionJournal:
    """Reconcile a journal against actual git state after an interruption.

    - A journal claiming a published candidate whose ref is missing is a
      typed FAILED (the publication did not survive; the claim releases).
    - A PREPARED/REVIEWED journal with no candidate side effects is RECOVERED:
      safe to rebuild and re-review; no shared state was touched.
    - Published state whose ref resolves is confirmed as-is (no transition).
    Tombstoned journals never change.
    """
    journal = read_journal(repo_root, transaction_id)
    if journal is None:
        raise GitTransactionError(f"no journal for transaction {transaction_id}")
    if journal.tombstoned:
        return journal
    if journal.candidate_ref:
        try:
            resolved = _git(repo_root, "rev-parse", "--verify", journal.candidate_ref).strip()
        except GitTransactionError:
            resolved = ""
        if resolved != journal.candidate_oid:
            failed = _advance(journal, TransactionState.FAILED, reason="published_candidate_ref_missing")
            write_journal(repo_root, failed)
            release_claim(repo_root, transaction_id)
            return failed
        return journal  # published and intact — nothing to recover
    recovered = _advance(journal, TransactionState.RECOVERED, recovery_action="safe_to_rebuild_and_re_review")
    write_journal(repo_root, recovered)
    return recovered


# ---------------------------------------------------------------------------
# FR05 — retention, cleanup, tombstones
# ---------------------------------------------------------------------------


def cleanup_candidates(
    repo_root: Path,
    *,
    now_epoch_days: int,
    referenced_transaction_ids: frozenset[str] = frozenset(),
) -> dict[str, list[str]]:
    """Collect eligible candidate refs; protect pinned/handoff refs (FR05).

    ``now_epoch_days`` is an injected day counter (no ambient clock in the
    decision). A candidate collects when its journal age exceeds
    CANDIDATE_RETENTION_DAYS and it is neither handoff-ready, under review,
    nor named in ``referenced_transaction_ids`` (checkpoint-referenced refs
    retain through run archival). Collected transactions are TOMBSTONED
    (journal retained) so the transaction id can never be reused; tombstones
    persist a year.
    """
    from trw_mcp.models.git_commit_transaction import CANDIDATE_RETENTION_DAYS

    journals_dir = repo_root / JOURNALS_RELATIVE_DIR
    collected: list[str] = []
    retained: list[str] = []
    if not journals_dir.is_dir():
        return {"collected": [], "retained": []}
    for journal_file in sorted(journals_dir.glob("*.json")):
        journal = read_journal(repo_root, journal_file.stem)
        if journal is None or journal.tombstoned:
            continue
        created_days = int(journal_file.stat().st_mtime // 86400)
        age = now_epoch_days - created_days
        protected = (
            str(journal.state)
            in (
                TransactionState.HANDOFF_READY.value,
                TransactionState.REVIEWED.value,
            )
            or journal.transaction_id in referenced_transaction_ids
        )
        if age > CANDIDATE_RETENTION_DAYS and not protected:
            if journal.candidate_ref:
                try:
                    _git(repo_root, "update-ref", "-d", journal.candidate_ref, journal.candidate_oid)
                except GitTransactionError:
                    logger.warning("candidate_ref_delete_failed", ref=journal.candidate_ref)
            write_journal(
                repo_root,
                _advance(journal, TransactionState.FAILED, reason="retention_collected").model_copy(
                    update={"tombstoned": True}
                ),
            )
            release_claim(repo_root, journal.transaction_id)
            collected.append(journal.transaction_id)
        else:
            retained.append(journal.transaction_id)
    return {"collected": collected, "retained": retained}
