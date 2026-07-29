"""FR01 + FR02 pre-push-stage entry point (id ``intent-signed-commit-check``).

Runs BOTH push-time controls over the SAME outgoing range in one invocation:

* FR01 — every commit that weakens a binding claim or its control plane must
  carry a signature ``git verify-commit`` accepts;
* FR02 — the split-commit range scan (weaken at A, edit anchors at B).

Range resolution prefers pre-commit's ``PRE_COMMIT_FROM_REF``/``PRE_COMMIT_TO_REF``
env vars, then the git pre-push stdin ref lines, then HEAD as a new ref. An
unresolvable range fails CLOSED.

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

import os
import sys
from typing import IO

from trw_mcp.security.intent_contract._git_run import GitCommandError, run_git
from trw_mcp.security.intent_contract._hook_common import HookDecision, enrollment_gate
from trw_mcp.security.intent_contract._revisions import ZERO_SHA
from trw_mcp.security.intent_contract.signed_commit import check_commit_range
from trw_mcp.security.intent_contract.weaken_edit_detector import (
    UNANSWERABLE_CLAIM_ID,
    detect_range_weaken_then_edit,
)

__all__ = ["main", "resolve_ranges"]

_BLOCK = 1


def resolve_ranges(stream: IO[str] | None = None) -> list[tuple[str, str]]:
    """``[(base_sha, head_sha)]`` for the outgoing push."""
    from_ref = os.environ.get("PRE_COMMIT_FROM_REF", "")
    to_ref = os.environ.get("PRE_COMMIT_TO_REF", "")
    if to_ref:
        return [(from_ref or ZERO_SHA, to_ref)]

    ranges: list[tuple[str, str]] = []
    if stream is not None and not stream.isatty():
        for line in stream:
            fields = line.split()
            if len(fields) == 4:
                ranges.append((fields[3], fields[1]))
    return ranges


def main(argv: list[str] | None = None, stream: IO[str] | None = None) -> int:
    del argv
    gate = enrollment_gate(_BLOCK)
    if isinstance(gate, HookDecision):
        return gate.code
    root = gate.root

    ranges = resolve_ranges(stream if stream is not None else sys.stdin)
    if not ranges:
        try:
            head = run_git(root, "rev-parse", "HEAD").strip()
        except GitCommandError as exc:
            # NOT "nothing to check" — git could not ANSWER, and this module's own
            # docstring already promised an unresolvable range fails CLOSED. The
            # `return 0` that used to live here was reachable with the same
            # two-line `git` stub as N10 and turned BOTH push-time controls off.
            print(
                "BLOCKED (intent-contract): the outgoing push range could not be resolved "
                f"({type(exc).__name__}) — refusing to pass a push that cannot be inspected.",
                file=sys.stderr,
            )
            return _BLOCK
        ranges = [(ZERO_SHA, head)]

    blocked = False
    for base_sha, head_sha in ranges:
        if set(head_sha) == {"0"}:  # branch deletion — nothing to inspect
            continue
        try:
            violations = check_commit_range(root, base_sha, head_sha)
            findings = detect_range_weaken_then_edit(root, base_sha, head_sha)
        except Exception as exc:
            print(f"BLOCKED (intent-contract): push check failed closed ({type(exc).__name__})", file=sys.stderr)
            return _BLOCK
        for violation in violations:
            blocked = True
            print(
                f"BLOCKED (intent-contract signature gate): {violation.sha} weakens a binding claim\n"
                f"  reason: {violation.reason} ({violation.detail})\n"
                f"  conditions: {', '.join(f'{h.condition} {h.claim_id}: {h.detail}' for h in violation.weakened)}",
                file=sys.stderr,
            )
        for finding in findings:
            blocked = True
            if finding.claim_id == UNANSWERABLE_CLAIM_ID:
                # An unrunnable check is not a detected weakening. Say which it is.
                print(
                    "BLOCKED (intent-contract weaken-then-edit range): the range could not be\n"
                    f"  inspected at {finding.editing_sha} — this is NOT a report that a claim was\n"
                    f"  weakened.\n  detail: {finding.detail}",
                    file=sys.stderr,
                )
                continue
            print(
                f"BLOCKED (intent-contract weaken-then-edit range): claim {finding.claim_id}\n"
                f"  weakened at {finding.weakening_sha}, anchors edited at {finding.editing_sha}\n"
                f"  edited: {', '.join(finding.edited_paths)}\n  detail: {finding.detail}",
                file=sys.stderr,
            )
    return _BLOCK if blocked else 0


if __name__ == "__main__":  # pragma: no cover — CLI entry
    raise SystemExit(main())
