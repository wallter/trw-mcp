"""Receiver-verdict and aggregate helpers for ``factory status`` (PRD-CORE-340 FR09/NFR03).

Descriptive only: a verdict says what a referenced verification receipt records
and whether it still revalidates now; it never grants acceptance.

Exit codes are pinned by tests: a recorded ``failed`` is a legitimate result (no diagnostic, exit 0);
``unverified`` is an integrity diagnostic (exit 1). With several USED verification references any
FAIL makes the attempt ``failed``, else any unverified makes it ``unverified``, else it completes.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any

from trw_mcp.models._evidence_core import EvidenceLimits, ReceiptState, domain_digest
from trw_mcp.models._evidence_plans import VerificationOutcome
from trw_mcp.models._evidence_records import VerificationReceipt
from trw_mcp.state._evidence_binding import content_binding_is_current
from trw_mcp.state._factory_read import read_bounded, run_relative
from trw_mcp.state._verification_artifact import verification_artifact_is_current

__all__ = ["aggregate_counts", "verification_verdict"]


_GIT_TIMEOUT_S = 10
_BLOB_CAP = 16 * 1024 * 1024


def _git_env() -> dict[str, str]:
    """A hermetic env: no inherited GIT_* (redirects, config), no lazy fetch, no prompt."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return {**env, "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}


def _safe_entry_path(path: str) -> bool:
    return bool(path) and not (path.startswith(("/", "./", "-")) or ".." in path.split("/") or ":" in path)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 - fixed argv, read-only, no network
        ["git", "-C", str(root), "cat-file", *args],  # noqa: S607
        capture_output=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
        env=_git_env(),
    )


def _bound_at_subject(receipt: VerificationReceipt, project_root: Path | None) -> bool:
    """True when every bound file's SHA-256 matches its blob at ``subject_sha`` (git objects are immutable).

    Any doubt (no project root, unsafe path, oversize blob, git failure or timeout) is False.
    """
    entries = [e for e in receipt.content_binding.entries if e.byte_digest]
    if project_root is None or not receipt.subject_sha or not entries:
        return False
    for entry in entries:
        if not _safe_entry_path(entry.path):
            return False
        spec = f"{receipt.subject_sha}:{entry.path}"
        try:
            size = _git(project_root, "-s", spec)
            if size.returncode != 0 or int(size.stdout.strip() or b"-1") > _BLOB_CAP:
                return False
            blob = _git(project_root, "blob", spec)
        except (OSError, ValueError, subprocess.SubprocessError):  # trw-fail-silent-allow: reported as a flag
            return False
        if blob.returncode != 0 or hashlib.sha256(blob.stdout).hexdigest() != entry.byte_digest:
            return False
    return True


def _freshness(receipt: VerificationReceipt, project_root: Path | None) -> tuple[bool, str]:
    """Return (stale, evidence_invalid reason) against today's tree; the two are exclusive.

    The trust gate's predicate is content_binding_is_current plus verification_artifact_is_current; run
    separately so a digest mismatch (``STALE_CONTENT``: the evidence moved) is told apart from evidence
    that cannot be judged (missing, unreadable, escaping, unstable): that is the reason code, not stale.
    """
    if project_root is None:
        return False, ""
    for outcome in (
        content_binding_is_current(receipt.content_binding, project_root),
        verification_artifact_is_current(receipt, project_root),
    ):
        if outcome.state is ReceiptState.STALE_CONTENT:
            return True, ""
        if outcome.state is not ReceiptState.VALID:
            return False, outcome.reason_code
    return False, ""


def verification_verdict(
    run: Path, ref: str | dict[str, Any], project_root: Path | None
) -> tuple[str, dict[str, bool | str]]:
    """Return (``pass``|``fail``|``unverified``, flags) for one already-resolved verification reference.

    ``project_root`` is ``<root>`` only when the run lives under ``<root>/.trw/runs``; otherwise None
    and no git check is attempted (``binding_unverifiable``). Never guessed.

    Integrity, exactly what is checked: the bytes parse as a VerificationReceipt, its ``receipt_id``
    equals the reference, and its ``mapping_digest`` equals the writer's digest of (subject_sha, check).
    Otherwise ``unverified``. NOT checked: ``outcome`` is covered by neither the digest nor a signature,
    so a same-user edit of a receipt file can flip FAIL to PASS (see LIMITATIONS).
    Outcome: the RECORDED ``outcome`` (PASS -> ``pass``, FAIL -> ``fail``; inconclusive/not_run ->
    ``unverified``). Freshness is separate and never changes the verdict: ``stale`` is True only when
    the evidence content changed (a digest mismatch against today's files); any other reason the current
    tree cannot vouch for it (missing, unreadable, escaping, unstable) is ``evidence_invalid`` carrying
    the reason code. Both are read-only, load no config, write nothing, and describe the tree now.
    ``binding_unverifiable`` is True unless every bound file matches its git blob at ``subject_sha``.
    """
    rid = ref if isinstance(ref, str) else ref["receipt_id"]
    # Containment, re-checked here rather than trusting the caller's _resolve: a cross-run owner must lie under
    # the project's runs dir (or the run itself), and is read by a no-follow walk from there, never resolved.
    anchor, prefix = run, ""
    if not isinstance(ref, str):
        base = project_root / ".trw" / "runs" if project_root else run
        rel = run_relative(base, project_root or run, ref["run_path"])
        if rel is None:
            return "unverified", {}
        anchor, prefix = base, f"{rel}/"
    raw, _ = read_bounded(
        anchor, f"{prefix}meta/receipts/verification/{rid}.json", EvidenceLimits.MAX_CANONICAL_RECEIPT_BYTES
    )
    if raw is None:
        return "unverified", {}
    try:
        receipt = VerificationReceipt.model_validate_json(raw)
    except ValueError:  # trw-fail-silent-allow: an unparsable receipt is reported as ``unverified``
        return "unverified", {}
    mapping = domain_digest(
        "verification_mapping", {"subject": receipt.subject_sha, "check": receipt.pass_condition_evaluation}
    )
    if receipt.receipt_id != rid or not receipt.matches_mapping(mapping):
        return "unverified", {}
    if receipt.outcome is VerificationOutcome.FAIL:
        return "fail", {}
    if receipt.outcome is not VerificationOutcome.PASS:
        return "unverified", {}
    stale, invalid = _freshness(receipt, project_root)
    return "pass", {
        "stale": stale,
        "evidence_invalid": invalid,
        "binding_unverifiable": not _bound_at_subject(receipt, project_root),
    }


def aggregate_counts(reports: list[dict[str, Any]]) -> dict[str, int]:
    """Cross-run counts printed next to the denominators (NFR03); new keys only add.

    ``open`` = attempts still short of a measured outcome (incomplete + unresolved + unverified).
    ``stale`` / ``evidence_invalid`` / ``binding_unverifiable`` count attempts carrying that flag; they never exclude.
    ``failed`` and ``voided`` are finished-but-not-completed and are not open.
    """
    total = dict.fromkeys(
        (
            "completed",
            "open",
            "failed",
            "voided",
            "excluded",
            "conflicting",
            "malformed",
            "stale",
            "evidence_invalid",
            "binding_unverifiable",
        ),
        0,
    )
    for r in reports:
        c = r.get("counts", {})
        total["completed"] += c.get("completed", 0)
        total["open"] += c.get("incomplete", 0) + c.get("unresolved", 0) + c.get("unverified", 0)
        for k in ("failed", "voided", "excluded"):
            total[k] += c.get(k, 0)
        total["conflicting"] += r.get("conflicting", 0)
        total["malformed"] += r.get("malformed", 0)
        for flag in ("stale", "evidence_invalid", "binding_unverifiable"):
            total[flag] += sum(1 for a in r.get("attempts", []) if a.get(flag))
    return total
