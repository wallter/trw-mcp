"""FR01: signature gate for commits that weaken a binding claim or its control plane.

Evaluates the shared C1-C9 predicate over EVERY revision pair in the outgoing
range (root commits, every merge parent, zero-SHA new refs, force-push ranges),
reading contract blobs BY OID with ``GIT_NO_REPLACE_OBJECTS=1``. Any pair with a
weakening hit must carry a commit signature that ``git verify-commit`` accepts.

Reason codes distinguish a genuine bad/absent signature (``signature_invalid``)
from an environment defect (``verification_unconfigured`` — e.g. ``gpg.format=ssh``
with no ``gpg.ssh.allowedSignersFile``, verified 2026-07-24: exit 1 with a
distinct, parseable error). Both fail closed; only the operator message differs.

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.security.intent_contract._control_plane import (
    BlobReader,
    configured_contract_path,
    control_plane_findings,
)
from trw_mcp.security.intent_contract._git_run import BlobUnreadable, GitCommandError, read_blob, run_git
from trw_mcp.security.intent_contract._models import Contract, SignedCommitViolation, WeakenedClaim
from trw_mcp.security.intent_contract._revisions import (
    RangeResolutionError,
    RevisionPair,
    enumerate_revision_pairs,
)
from trw_mcp.security.intent_contract._weaken_predicate import (
    evaluate_claim_weakening,
    evaluate_locator_weakening,
)
from trw_mcp.security.intent_contract.loader import ContractLoadError, load_contract_bytes

__all__ = ["blob_reader", "check_commit_range", "removed_or_renamed_paths", "weakening_for_pair"]

#: stderr fragments that mean "verification could not run", not "signature is bad".
_UNCONFIGURED_MARKERS = (
    "allowedsignersfile",
    "needs to be configured",
    "command not found",
    "no such file or directory",
)


def blob_reader(repo_root: Path, rev: str | None) -> BlobReader:
    """A path -> bytes reader pinned to one revision (``None`` = empty tree)."""

    def _read(path: str) -> bytes | None:
        return read_blob(repo_root, rev, path)

    return _read


def removed_or_renamed_paths(repo_root: Path, pair: RevisionPair) -> frozenset[str]:
    """Base-side paths deleted or renamed between the pair (``git diff -M``)."""
    if pair.base is None:
        return frozenset()
    try:
        raw = run_git(repo_root, "diff", "-M", "--name-status", "-z", pair.base, pair.candidate)
    except GitCommandError:
        return frozenset()
    fields = [field for field in raw.split("\0") if field]
    removed: set[str] = set()
    index = 0
    while index < len(fields):
        status = fields[index]
        if status.startswith("R") and index + 2 < len(fields):
            removed.add(fields[index + 1])
            index += 3
            continue
        if status.startswith("D") and index + 1 < len(fields):
            removed.add(fields[index + 1])
        index += 2
    return frozenset(removed)


def _snapshot(read: BlobReader) -> tuple[Contract | None, str | None]:
    """Load a side's contract; return ``(contract, load_failure_reason)``.

    ``(None, None)`` — the benign, silent outcome — is reserved for a tree that
    genuinely has no contract in it. A read that could not be ANSWERED comes back
    as a failure reason, which :func:`weakening_for_pair` turns into a finding
    and therefore into a required signature (finding F-B).
    """
    try:
        path = configured_contract_path(read)
        raw = read(path)
    except BlobUnreadable as exc:
        return None, f"contract could not be read from this rev: {exc}"
    if raw is None:
        return None, None
    try:
        return load_contract_bytes(raw), None
    except ContractLoadError as exc:
        return None, exc.reason


def weakening_for_pair(repo_root: Path, pair: RevisionPair) -> tuple[WeakenedClaim, ...]:
    """Every C1-C9 hit for one ``(base, candidate)`` pair."""
    read_base = blob_reader(repo_root, pair.base)
    read_candidate = blob_reader(repo_root, pair.candidate)
    base_contract, base_failure = _snapshot(read_base)
    candidate_contract, candidate_failure = _snapshot(read_candidate)

    try:
        findings = list(
            control_plane_findings(
                read_base, read_candidate, removed_or_renamed=removed_or_renamed_paths(repo_root, pair)
            )
        )
    except BlobUnreadable as exc:
        # C9 reads ~8 control-plane paths per side and treats an absent one as
        # "nothing was registered here". An unanswerable read must not join that
        # set: it costs the signature instead (finding F-B).
        findings = [f"control-plane snapshot could not be read: {exc}"]
    # A contract that will not load is not a benign edit: it disarms every
    # runtime control point, so it costs the same signature (fail closed).
    if base_failure:
        findings.append(f"contract unloadable at base: {base_failure}")
    if candidate_failure:
        findings.append(f"contract unloadable at candidate: {candidate_failure}")

    hits = list(evaluate_claim_weakening(base_contract, candidate_contract))
    hits.extend(evaluate_locator_weakening(findings))
    return tuple(hits)


def _verify_commit(repo_root: Path, sha: str) -> tuple[bool, str, str]:
    """Return ``(verified, reason, detail)`` for ``git verify-commit``."""
    try:
        run_git(repo_root, "verify-commit", sha)
    except GitCommandError as exc:
        lowered = exc.stderr.lower()
        reason = (
            "verification_unconfigured"
            if any(marker in lowered for marker in _UNCONFIGURED_MARKERS)
            else "signature_invalid"
        )
        return False, reason, exc.stderr.strip().splitlines()[0] if exc.stderr.strip() else "no signature found"
    return True, "", ""


def check_commit_range(
    repo_root: Path,
    base_sha: str | None,
    head_sha: str,
) -> list[SignedCommitViolation]:
    """Every unsigned weakening commit in the outgoing range.

    An unresolvable range (pruned/rewritten base) yields a single
    ``unresolvable_range`` violation — the push is rejected rather than silently
    passing an unverifiable baseline.
    """
    try:
        pairs = enumerate_revision_pairs(repo_root, base_sha, head_sha)
    except RangeResolutionError as exc:
        return [SignedCommitViolation(sha=head_sha, reason="unresolvable_range", detail=str(exc))]

    violations: list[SignedCommitViolation] = []
    verified_cache: dict[str, tuple[bool, str, str]] = {}
    for pair in pairs:
        hits = weakening_for_pair(repo_root, pair)
        if not hits:
            continue
        if pair.candidate not in verified_cache:
            verified_cache[pair.candidate] = _verify_commit(repo_root, pair.candidate)
        verified, reason, detail = verified_cache[pair.candidate]
        if verified:
            continue
        existing = next((v for v in violations if v.sha == pair.candidate), None)
        if existing is not None:
            violations.remove(existing)
            hits = tuple(dict.fromkeys((*existing.weakened, *hits)))
        violations.append(
            SignedCommitViolation(
                sha=pair.candidate,
                reason="verification_unconfigured" if reason == "verification_unconfigured" else "signature_invalid",
                weakened=hits,
                detail=detail,
            )
        )
    return violations
