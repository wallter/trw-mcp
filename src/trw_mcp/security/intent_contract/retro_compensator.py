"""FR08: retroactive weaken-then-edit detection over recent history.

This is the only layer that survives ``--no-verify`` and a force-push around the
git hooks: it is a plain history walk driven by the project's standing test
suite, not a git hook, so skipping the hooks does not skip it. It does NOT
prevent an out-of-band bypass — it makes one DETECTABLE on the next run
(RISK-001, stated honestly rather than implied away).

Cost control (NFR02, <= 10s over a 500-commit window): one ``git log`` pass
collects per-commit paths, and the expensive C1-C9 evaluation only runs for
commits that actually touch a contract or control-plane path.

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.security.intent_contract._anchors import anchor_matches
from trw_mcp.security.intent_contract._control_plane import (
    DEFAULT_CONTRACT_PATH,
    PRE_COMMIT_CONFIG_PATH,
    SETTINGS_SOURCE_PATH,
    TRW_CONFIG_PATH,
    BlobReader,
    configured_contract_path,
)
from trw_mcp.security.intent_contract._git_run import BlobUnreadable, GitCommandError, run_git
from trw_mcp.security.intent_contract._models import WeakenEditFinding
from trw_mcp.security.intent_contract._revisions import commit_pairs
from trw_mcp.security.intent_contract.ledger import ledger_path
from trw_mcp.security.intent_contract.loader import ContractLoadError, load_contract_bytes
from trw_mcp.security.intent_contract.signed_commit import blob_reader, weakening_for_pair
from trw_mcp.security.intent_contract.weaken_edit_detector import (
    UNANSWERABLE_CLAIM_ID,
    approval_exists,
    commit_diff_hash,
)

__all__ = ["ledger_has_override", "recent_commits_with_paths", "scan_recent_history"]

_CONTROL_PLANE_PREFIXES = (".trw/contracts/",)
_CONTROL_PLANE_FILES = (TRW_CONFIG_PATH, SETTINGS_SOURCE_PATH, PRE_COMMIT_CONFIG_PATH, DEFAULT_CONTRACT_PATH)


def recent_commits_with_paths(repo_root: Path, window_commits: int) -> list[tuple[str, tuple[str, ...]]]:
    """``[(sha, changed_paths)]`` oldest-first, in ONE ``git log`` invocation."""
    try:
        raw = run_git(
            repo_root,
            "log",
            f"-{window_commits}",
            "--first-parent",
            "--reverse",
            "--name-only",
            # 0x1e (record separator) delimits commits; -z NUL-delimits paths, so
            # neither separator can appear inside a path.
            "--format=%x1e%H",
            "-z",
        )
    except GitCommandError:
        return []
    commits: list[tuple[str, tuple[str, ...]]] = []
    for chunk in raw.split("\x1e"):
        fields = [field.strip("\n") for field in chunk.split("\0")]
        sha = fields[0].strip() if fields else ""
        if not sha:
            continue
        commits.append((sha, tuple(path for path in fields[1:] if path)))
    return commits


def _is_control_plane(paths: tuple[str, ...], contract_path: str) -> bool:
    for path in paths:
        if path.startswith(_CONTROL_PLANE_PREFIXES) or path in _CONTROL_PLANE_FILES or path == contract_path:
            return True
    return False


def ledger_has_override(repo_root: Path, claim_id: str) -> bool:
    """True when the FR03 ledger already records an override for *claim_id*."""
    path = ledger_path(repo_root)
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("claim_id") == claim_id and entry.get("kind") == "override":
            return True
    return False


def scan_recent_history(
    repo_root: Path, window_commits: int = 500, session_id: str | None = None
) -> list[WeakenEditFinding]:
    """Recompute FR02's predicate over the last *window_commits* commits."""
    commits = recent_commits_with_paths(repo_root, window_commits)
    if not commits:
        return []
    head_reader = blob_reader(repo_root, "HEAD")
    try:
        contract_path = configured_contract_path(head_reader)
    except BlobUnreadable as exc:
        # Falling back to the DEFAULT path here would narrow the whole history
        # walk to commits touching a file that may not be the contract, and
        # report "no findings" for a scan that never looked (finding F-B).
        return [_unanswerable(f"contract locator at HEAD could not be read: {exc}")]

    findings: list[WeakenEditFinding] = []
    pending: dict[str, tuple[str, str, tuple[str, ...]]] = {}
    for sha, paths in commits:
        if _is_control_plane(paths, contract_path):
            try:
                pairs = commit_pairs(repo_root, sha)
            except GitCommandError:
                pairs = []
            for pair in pairs:
                base_contract_reader = blob_reader(repo_root, pair.base)
                for hit in weakening_for_pair(repo_root, pair):
                    try:
                        anchors = _anchors_for(base_contract_reader, hit.claim_id)
                    except BlobUnreadable as exc:
                        findings.append(_unanswerable(f"anchors for {hit.claim_id} at {sha} unreadable: {exc}", sha))
                        continue
                    if anchors:
                        pending[hit.claim_id] = (sha, hit.condition, anchors)

        for claim_id, (weakening_sha, condition, anchors) in list(pending.items()):
            touched = tuple(path for path in paths if any(anchor_matches(a, path) for a in anchors))
            if not touched:
                continue
            approved = approval_exists(repo_root, commit_diff_hash(repo_root, sha), session_id)
            if ledger_has_override(repo_root, claim_id) or approved:
                pending.pop(claim_id, None)
                continue
            pending.pop(claim_id, None)
            findings.append(
                WeakenEditFinding(
                    claim_id=claim_id,
                    condition=condition,
                    weakening_sha=weakening_sha,
                    editing_sha=sha,
                    edited_paths=touched,
                    diff_hash="",
                    detail="unapproved weaken-then-edit found retroactively (hooks skipped or bypassed)",
                )
            )
    return findings


def _unanswerable(detail: str, sha: str = "<head>") -> WeakenEditFinding:
    """A scan step that could not RUN, reported as a finding rather than silence.

    Shares :data:`UNANSWERABLE_CLAIM_ID` with the FR02 detectors so operator-facing
    callers keep one branch for "could not be evaluated" and never render it as
    "you weakened a claim".
    """
    return WeakenEditFinding(
        claim_id=UNANSWERABLE_CLAIM_ID,
        condition="C9",
        weakening_sha=sha,
        editing_sha=sha,
        edited_paths=(),
        diff_hash="",
        detail=f"retroactive weaken-then-edit scan could not be evaluated: {detail}",
    )


def _anchors_for(read: BlobReader, claim_id: str) -> tuple[str, ...]:
    raw = read(configured_contract_path(read))
    if raw is None:
        return ()
    try:
        contract = load_contract_bytes(raw)
    except ContractLoadError:
        return ()
    claim = contract.by_id().get(claim_id)
    return claim.anchors if claim is not None else ()
