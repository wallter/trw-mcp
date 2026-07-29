"""FR02: weaken-then-edit detection — same commit, staged tree, and outgoing range.

Three modes over ONE predicate:

* :func:`detect_same_commit_weaken` — the L-2EW1 shape: one commit both edits a
  claim-anchored file and weakens the governing claim.
* :func:`detect_staged_weaken` — the same check against the currently-staged
  tree, which is what the pre-commit-stage hook can see.
* :func:`detect_range_weaken_then_edit` — commit A weakens, commit B edits later
  in the same outgoing range (codex #5's split-commit escape). Reverting the
  weakening, or superseding the claim with a covering one, clears the pending
  flag before a later edit can trip it (OQ10's edge cases, resolved here by test).

An approval record from a DIFFERENT session clears a finding. **Threat model —
honest limits (probe-verified 2026-07-24), both halves:**

1. *Weak identity.* That clearing rule is a time/turn gate over the available
   primitives, not an identity system (Non-Goals) — TRW has no cross-session
   agent identity, so an agent that can wait a turn can also author the record it
   is cleared by. Unclosed in v0.
2. *Evidence visibility.* The gate above is worth exactly as much as C9's ability
   to SEE the record. ``.trw/.gitignore`` ignores ``*.jsonl``, which silently hid
   BOTH this approvals file AND the override ledger from every git-side check —
   so a forged approval was not merely weakly identified, it was invisible. Two
   negation rules (``!contracts/weaken-edit-approvals.jsonl`` and
   ``!contracts/intent-override-ledger.jsonl``) now force-track them, and removing
   either rule is itself a C9 control-plane finding
   (``_control_plane.EVIDENCE_VISIBILITY_RULES``). Closed in this revision; the
   half above is not, which is why both are stated here.

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from trw_mcp.security.intent_contract._anchors import anchor_matches, claims_matching_path
from trw_mcp.security.intent_contract._atomic_json import locked
from trw_mcp.security.intent_contract._control_plane import configured_contract_path
from trw_mcp.security.intent_contract._git_run import BlobUnreadable, GitCommandError, run_git, run_git_bytes
from trw_mcp.security.intent_contract._models import Contract, MustNotHappenClaim, WeakenEditFinding
from trw_mcp.security.intent_contract._revisions import (
    RangeResolutionError,
    RevisionPair,
    commit_pairs,
    enumerate_revision_pairs,
)
from trw_mcp.security.intent_contract._weaken_predicate import (
    evaluate_claim_weakening,
    is_valid_supersession,
)
from trw_mcp.security.intent_contract.loader import ContractLoadError, load_contract_bytes
from trw_mcp.security.intent_contract.paths import APPROVALS_PATH
from trw_mcp.security.intent_contract.signed_commit import blob_reader, weakening_for_pair

__all__ = [
    "UNANSWERABLE_CLAIM_ID",
    "approval_exists",
    "approvals_path",
    "commit_diff_hash",
    "detect_range_weaken_then_edit",
    "detect_same_commit_weaken",
    "detect_staged_weaken",
    "record_approval",
]


#: ``claim_id`` of a fail-closed finding that names an UNRUNNABLE check rather
#: than a detected weakening. Callers branch on it so the operator message says
#: "could not be evaluated", never "you weakened a claim".
UNANSWERABLE_CLAIM_ID = "<unanswerable>"


class _Unanswerable(RuntimeError):
    """A query this detector depends on could not be answered.

    Distinct from "the query answered no". Every ``except ... : return None`` /
    ``return ()`` in this module used to erase that distinction, and ``None``
    means "no finding" all the way out to exit 0: with healthy git a staged
    weaken-then-edit gave ``1 + BLOCKED``, and the SAME staged state under a
    two-line ``git`` stub gave a silent 0 with enrollment still reading
    ``current`` (finding F3, 2026-07-25). The rule is the one
    :mod:`trw_mcp.security.intent_contract.pre_push_check` already states: a
    check that could not run is not a check that passed.
    """


def _unanswerable_finding(sha: str, detail: str) -> WeakenEditFinding:
    """Fail CLOSED as a first-class finding, so the caller's message says why."""
    return WeakenEditFinding(
        claim_id=UNANSWERABLE_CLAIM_ID,
        condition="C9",
        weakening_sha=sha,
        editing_sha=sha,
        edited_paths=(),
        diff_hash="",
        detail=f"weaken-then-edit check could not be evaluated: {detail}",
    )


@dataclass(frozen=True)
class _Pending:
    weakening_sha: str
    condition: str
    anchors: tuple[str, ...]
    original: MustNotHappenClaim | None


def approvals_path(root: Path) -> Path:
    return root / APPROVALS_PATH


def record_approval(root: Path, *, diff_hash: str, session_id: str, claim_id: str, note: str) -> dict[str, str]:
    """Append an independent-session approval for one exact diff.

    Goes through :func:`locked` — the same ``O_NOFOLLOW`` + ``O_APPEND`` + advisory
    lock skeleton the ledger and the open-violation marker use. It was the last
    plain ``path.open("a")`` in the package, i.e. the last write that a symlink at
    a control-plane path could retarget (the F-D shape, swept for rather than
    probed). The lock is not incidental either: approvals are appended by
    concurrent sessions by design.
    """
    if not note.strip():
        raise ValueError("approval note must be non-empty")
    entry = {
        "diff_hash": diff_hash,
        "session_id": session_id,
        "claim_id": claim_id,
        "note": note.strip(),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    with locked(approvals_path(root), append=True) as descriptor:
        os.write(descriptor, (json.dumps(entry, sort_keys=True) + "\n").encode("utf-8"))
    return entry


def approval_exists(root: Path, diff_hash: str, current_session_id: str | None = None) -> bool:
    """True when a prior, DIFFERENT-session approval matches *diff_hash*."""
    path = approvals_path(root)
    if not diff_hash or not path.exists():
        return False
    session = current_session_id if current_session_id is not None else os.environ.get("TRW_SESSION_ID", "")
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("diff_hash") != diff_hash:
            continue
        recorded_session = str(entry.get("session_id", ""))
        if recorded_session and recorded_session != session:
            return True
    return False


def _contract_at(repo_root: Path, rev: str | None) -> Contract | None:
    """The contract snapshot at *rev*, or ``None`` when there genuinely is none.

    A contract that is PRESENT but will not load is not the same as an absent
    one, and it is the more dangerous of the two on the BASE side: an empty base
    snapshot means "nothing was protected here", so every later weakening
    compares against nothing and no finding is ever produced.

    A contract that could not be FETCHED is the same hazard one query deeper, and
    it was still open after the round-3 sweep: this function only raised for the
    present-but-unparsable case, so a ``read_blob`` that failed arrived here as a
    plain ``None`` and read as "there is genuinely no contract at this rev"
    (finding F-B). The reader now says which it was.
    """
    read = blob_reader(repo_root, rev)
    try:
        raw = read(configured_contract_path(read))
    except BlobUnreadable as exc:
        raise _Unanswerable(f"contract at {rev or '<empty tree>'} could not be read: {exc}") from exc
    if raw is None:
        return None
    try:
        return load_contract_bytes(raw)
    except ContractLoadError as exc:
        raise _Unanswerable(f"contract at {rev or '<empty tree>'} is unloadable: {exc.reason}") from exc


def _changed_paths(repo_root: Path, sha: str) -> tuple[str, ...]:
    try:
        raw = run_git(repo_root, "show", "--name-only", "--format=", "-z", sha)
    except GitCommandError as exc:
        raise _Unanswerable(f"`git show` failed for {sha} ({exc.returncode})") from exc
    return tuple(path for path in raw.split("\0") if path)


def commit_diff_hash(repo_root: Path, sha: str) -> str:
    try:
        return hashlib.sha256(run_git_bytes(repo_root, "show", "--format=", "-p", sha)).hexdigest()
    except GitCommandError:
        return ""


def _claim(contract: Contract | None, claim_id: str) -> MustNotHappenClaim | None:
    return contract.by_id().get(claim_id) if contract is not None else None


def _cleared(pending: _Pending, candidate: Contract | None, claim_id: str) -> bool:
    """True when the weakening was reverted, or superseded by a covering claim."""
    current = _claim(candidate, claim_id)
    if pending.original is None or current is None or candidate is None:
        return False
    return current == pending.original or is_valid_supersession(pending.original, current, candidate)


def _scan_pair(
    repo_root: Path,
    pair: RevisionPair,
    pending: dict[str, _Pending],
    session_id: str | None,
    editing_sha: str,
    edited: tuple[str, ...],
) -> WeakenEditFinding | None:
    """Refresh pendings from one pair, then flag any protected-path edit."""
    base_contract = _contract_at(repo_root, pair.base)
    candidate_contract = _contract_at(repo_root, pair.candidate)

    for claim_id, entry in list(pending.items()):
        if _cleared(entry, candidate_contract, claim_id):
            pending.pop(claim_id, None)

    for hit in weakening_for_pair(repo_root, pair):
        anchored = _claim(base_contract, hit.claim_id)
        anchors = anchored.anchors if anchored is not None else ()
        if anchors:
            pending[hit.claim_id] = _Pending(pair.candidate, hit.condition, anchors, anchored)

    for claim_id, entry in list(pending.items()):
        touched = tuple(path for path in edited if any(anchor_matches(a, path) for a in entry.anchors))
        if not touched:
            continue
        diff_hash = commit_diff_hash(repo_root, editing_sha) if editing_sha != "<staged>" else ""
        if approval_exists(repo_root, diff_hash, session_id):
            continue
        pending.pop(claim_id, None)
        return WeakenEditFinding(
            claim_id=claim_id,
            condition=entry.condition,
            weakening_sha=entry.weakening_sha,
            editing_sha=editing_sha,
            edited_paths=touched,
            diff_hash=diff_hash,
            detail="claim weakened and its protected anchors edited without an independent approval",
        )
    return None


def detect_same_commit_weaken(
    repo_root: Path, commit_sha: str, session_id: str | None = None
) -> WeakenEditFinding | None:
    """The L-2EW1 shape: one commit edits protected code AND weakens its claim."""
    try:
        edited = _changed_paths(repo_root, commit_sha)
        if not edited:
            return None
        pairs = commit_pairs(repo_root, commit_sha)
        for pair in pairs:
            finding = _scan_pair(repo_root, pair, {}, session_id, commit_sha, edited)
            if finding is not None:
                return finding
    except (GitCommandError, _Unanswerable) as exc:
        return _unanswerable_finding(commit_sha, str(exc))
    return None


def detect_staged_weaken(repo_root: Path, session_id: str | None = None) -> WeakenEditFinding | None:
    """Pre-commit-stage variant: HEAD vs the currently-staged tree."""
    try:
        staged = tuple(p for p in run_git(repo_root, "diff", "--cached", "--name-only", "-z").split("\0") if p)
        staged_diff = run_git_bytes(repo_root, "diff", "--cached")
    except GitCommandError as exc:
        # NOT "nothing is staged" — git could not ANSWER. This is the same rule
        # pre_push_check.py already applies to an unresolvable push range, and the
        # `return None` that used to live here was reachable with the same
        # two-line `git` stub: a staged weaken-then-edit that blocked under a
        # healthy git went silently to exit 0 under the stub (finding F3).
        return _unanswerable_finding("<staged>", f"`git diff --cached` failed ({exc.returncode})")
    if not staged:
        return None

    read_head = blob_reader(repo_root, "HEAD")
    try:
        # Resolving the LOCATOR is itself a blob read, and an unanswerable one
        # silently fell back to the default contract path — so a stub could point
        # the whole check at a file that does not exist and get "nothing staged
        # here" for free (finding F-B).
        contract_path = configured_contract_path(read_head)
        base_contract = _contract_at(repo_root, "HEAD")
    except BlobUnreadable as exc:
        return _unanswerable_finding("<staged>", f"HEAD contract locator could not be read: {exc}")
    except _Unanswerable as exc:
        return _unanswerable_finding("<staged>", str(exc))
    try:
        staged_raw: bytes | None = run_git_bytes(repo_root, "show", f":{contract_path}")
    except GitCommandError:
        staged_raw = None
    try:
        staged_contract = load_contract_bytes(staged_raw) if staged_raw is not None else None
    except ContractLoadError as exc:
        return WeakenEditFinding(
            claim_id="<contract>",
            condition="C9",
            weakening_sha="<staged>",
            editing_sha="<staged>",
            edited_paths=staged,
            diff_hash="",
            detail=f"staged contract is unloadable: {exc.reason}",
        )

    diff_hash = hashlib.sha256(staged_diff).hexdigest()
    for hit in evaluate_claim_weakening(base_contract, staged_contract):
        anchored = _claim(base_contract, hit.claim_id)
        touched = tuple(path for path in staged if anchored is not None and claims_matching_path((anchored,), path))
        if not touched or approval_exists(repo_root, diff_hash, session_id):
            continue
        return WeakenEditFinding(
            claim_id=hit.claim_id,
            condition=hit.condition,
            weakening_sha="<staged>",
            editing_sha="<staged>",
            edited_paths=touched,
            diff_hash=diff_hash,
            detail=hit.detail,
        )
    return None


def detect_range_weaken_then_edit(
    repo_root: Path, base_sha: str | None, head_sha: str, session_id: str | None = None
) -> list[WeakenEditFinding]:
    """Split-commit escape: A weakens, B edits the anchors later in the range."""
    try:
        pairs = enumerate_revision_pairs(repo_root, base_sha, head_sha)
    except RangeResolutionError as exc:
        return [
            WeakenEditFinding(
                claim_id="<range>",
                condition="C9",
                weakening_sha=head_sha,
                editing_sha=head_sha,
                edited_paths=(),
                diff_hash="",
                detail=f"unresolvable range: {exc}",
            )
        ]

    findings: list[WeakenEditFinding] = []
    pending: dict[str, _Pending] = {}
    seen: set[str] = set()
    for pair in pairs:
        if pair.candidate in seen:
            continue
        seen.add(pair.candidate)
        try:
            edited = _changed_paths(repo_root, pair.candidate)
            finding = _scan_pair(repo_root, pair, pending, session_id, pair.candidate, edited)
        except (GitCommandError, _Unanswerable) as exc:
            findings.append(_unanswerable_finding(pair.candidate, str(exc)))
            continue
        if finding is not None:
            findings.append(finding)
    return findings
