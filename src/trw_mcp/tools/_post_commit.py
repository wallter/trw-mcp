"""Combined TRW post-commit maintenance entry point (PRD-CORE-231 FR01/FR02).

A commit changes two things at once that TRW cares about:

* the HEAD sha, which invalidates the whole sha-keyed T2 hint sidecar (FR01);
* the working tree, which can make a learning's assertions or anchors go stale
  (FR02) — and staleness is otherwise only noticed if someone happens to recall
  that exact entry.

Both are therefore driven from ONE git ``post-commit`` hook. The sweep running
on every commit plus the documented nightly cadence is what bounds NFR02's
stale-claim latency to <=24h by construction.

Fail-open throughout: a git hook must never fail or stall ``git commit``.
Every step is individually guarded and a receipt is written regardless, so
"the hook fired" is observable even when the work inside it was a no-op.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

#: Written after every hook invocation. Operators (and the wiring test) use it
#: to prove the hook is actually installed and firing, which is precisely the
#: "delivered != wired" failure this file exists to close.
RECEIPT_REL_PATH = Path(".trw") / "runtime" / "post-commit-receipt.json"


@dataclass(slots=True)
class PostCommitReceipt:
    """Observable record of one post-commit maintenance run."""

    ran_at: str = ""
    head_sha: str = ""
    sidecar_files: int = 0
    sidecar_skipped_reason: str = ""
    verify_entries_processed: int = 0
    verify_stale_transitions: int = 0
    verify_cleared_transitions: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Plain mapping for the JSON receipt."""
        return {
            "ran_at": self.ran_at,
            "head_sha": self.head_sha,
            "sidecar_files": self.sidecar_files,
            "sidecar_skipped_reason": self.sidecar_skipped_reason,
            "verify_entries_processed": self.verify_entries_processed,
            "verify_stale_transitions": self.verify_stale_transitions,
            "verify_cleared_transitions": self.verify_cleared_transitions,
            "errors": self.errors,
        }


def _head_sha(repo_root: Path) -> str:
    try:
        from trw_mcp.tools._sidecar_substrate import resolve_git_sha

        return str(resolve_git_sha(repo_root) or "")
    except Exception:  # justified: fail-open, the sha is diagnostic only
        return ""


def run_post_commit(repo_root: Path, source_env: dict[str, str] | None = None) -> PostCommitReceipt:
    """Run every post-commit maintenance step and write the receipt.

    Args:
        repo_root: Repository the commit landed in.
        source_env: Environment to project onto the subprocess allowlist;
            defaults to the current process environment.

    Returns:
        A :class:`PostCommitReceipt`. Never raises.
    """
    receipt = PostCommitReceipt(
        ran_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        head_sha=_head_sha(repo_root),
    )

    # FR01 — re-seed the sha-keyed T2 sidecar for the files this commit touched.
    try:
        from trw_mcp.tools._hint_sidecar_refresh import run_post_commit_refresh

        plan = run_post_commit_refresh(repo_root, dict(source_env if source_env is not None else os.environ))
        receipt.sidecar_files = len(plan.files)
        receipt.sidecar_skipped_reason = plan.skipped_reason
    except Exception as exc:  # justified: fail-open, must never block git commit
        logger.debug("post_commit_sidecar_refresh_failed", exc_info=True)
        receipt.errors.append(f"sidecar_refresh: {exc}")

    # FR02 — re-verify assertions/anchors so staleness is bounded by the sweep
    # cadence rather than by whether anyone happened to recall the entry.
    try:
        from trw_mcp.tools._maintain_verify import run_maintain_verify_for_project

        summary = run_maintain_verify_for_project()
        receipt.verify_entries_processed = summary.entries_processed
        receipt.verify_stale_transitions = summary.stale_transitions
        receipt.verify_cleared_transitions = summary.cleared_transitions
    except Exception as exc:  # justified: fail-open, must never block git commit
        logger.debug("post_commit_maintain_verify_failed", exc_info=True)
        receipt.errors.append(f"maintain_verify: {exc}")

    _write_receipt(repo_root, receipt)
    logger.info(
        "post_commit_maintenance_complete",
        head_sha=receipt.head_sha,
        sidecar_files=receipt.sidecar_files,
        verify_entries_processed=receipt.verify_entries_processed,
        errors=len(receipt.errors),
    )
    return receipt


def _write_receipt(repo_root: Path, receipt: PostCommitReceipt) -> None:
    """Persist the receipt; a write failure is itself non-fatal."""
    try:
        path = repo_root / RECEIPT_REL_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt.as_dict(), indent=2), encoding="utf-8")
    except OSError:
        logger.debug("post_commit_receipt_write_failed", exc_info=True)


def read_receipt(repo_root: Path) -> dict[str, Any] | None:
    """Return the last post-commit receipt, or ``None`` when absent/unreadable."""
    try:
        raw = (repo_root / RECEIPT_REL_PATH).read_text(encoding="utf-8")
        parsed: Any = json.loads(raw)
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


__all__ = [
    "RECEIPT_REL_PATH",
    "PostCommitReceipt",
    "read_receipt",
    "run_post_commit",
]
