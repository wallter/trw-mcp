"""FR02 pre-commit-stage entry point (``.pre-commit-config.yaml`` id ``intent-weaken-edit-flag``).

Evaluates the staged tree: a commit that both edits a claim-anchored file and
weakens the governing claim is blocked unless a prior, independent-session
approval record matches that exact staged diff.

Exit 0 = allow, 1 = block (pre-commit's own convention), 0 also for the
never-enrolled no-op. Every unexpected error after enrollment is recognized fails
CLOSED.

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

import sys

from trw_mcp.security.intent_contract._hook_common import HookDecision, enrollment_gate
from trw_mcp.security.intent_contract.weaken_edit_detector import UNANSWERABLE_CLAIM_ID, detect_staged_weaken

__all__ = ["main"]

_BLOCK = 1


def main(argv: list[str] | None = None) -> int:
    del argv
    gate = enrollment_gate(_BLOCK)
    if isinstance(gate, HookDecision):
        return gate.code
    root = gate.root

    try:
        finding = detect_staged_weaken(root)
    except Exception as exc:
        print(
            f"BLOCKED (intent-contract): weaken-then-edit check failed closed ({type(exc).__name__})", file=sys.stderr
        )
        return _BLOCK
    if finding is None:
        return 0

    if finding.claim_id == UNANSWERABLE_CLAIM_ID:
        # Say what actually happened. Reporting an unrunnable check as a detected
        # weakening would be the truthfulness defect this control exists to catch.
        print(
            "BLOCKED (intent-contract weaken-then-edit): the check could not be evaluated,\n"
            "so it is NOT reporting that you weakened anything — it is refusing to pass a\n"
            "commit it was unable to inspect.\n"
            f"  detail: {finding.detail}\n\n"
            "ACTION: repair the repository or its git environment (`git rev-parse --git-dir`,\n"
            "`command -v -a git`) and commit again.",
            file=sys.stderr,
        )
        return _BLOCK

    print(
        "BLOCKED (intent-contract weaken-then-edit): this commit edits a claim-anchored\n"
        f"path AND weakens the governing claim in the same change.\n"
        f"  claim: {finding.claim_id} (condition {finding.condition})\n"
        f"  detail: {finding.detail}\n"
        f"  edited: {', '.join(finding.edited_paths)}\n"
        f"  diff_hash: {finding.diff_hash}\n\n"
        "ACTION: split the change, or record an independent-session approval for this\n"
        "exact diff hash before committing. A --no-verify bypass is still detected\n"
        "retroactively by the FR08 retro-compensator on the next make check.",
        file=sys.stderr,
    )
    return _BLOCK


if __name__ == "__main__":  # pragma: no cover — CLI entry
    raise SystemExit(main())
