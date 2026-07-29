"""FR07: the post-edit falsifier check, against the ACTUAL written file.

This is the control point that enforces falsifier correctness. It reads the real
post-edit bytes with ``O_NOFOLLOW`` (a symlink swapped in after the tool call
cannot retarget the read), runs the claim's structured falsifier against the true
final tree, and on failure:

* surfaces the claim text + failing falsifier (exit 2 — high-visibility feedback
  to the model, never assumed to hard-block: the write already happened);
* records a ledger violation entry; and
* persists an OPEN-VIOLATION MARKER, which is the hard enforcement — the existing
  deliver gate treats it as BLOCK-class until dispositioned
  (:func:`trw_mcp.security.intent_contract.violations.intent_violation_gate_block`).

Fail-closed is total here (R4): failure, timeout, unreadable file, unloadable
contract, and any uncaught exception all map to exit 2.

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

import os
import sys
from typing import IO

from trw_mcp.security.intent_contract._anchors import enforceable_claims
from trw_mcp.security.intent_contract._falsifier import falsifier_label, run_falsifier
from trw_mcp.security.intent_contract._hook_common import (
    ALLOW,
    BLOCK,
    HookDecision,
    HookInput,
    read_hook_input,
    telemetry,
)
from trw_mcp.security.intent_contract._models import MustNotHappenClaim
from trw_mcp.security.intent_contract.break_glass import consume_token
from trw_mcp.security.intent_contract.ledger import LedgerError, record_override, record_violation
from trw_mcp.security.intent_contract.paths import read_bytes_nofollow
from trw_mcp.security.intent_contract.violations import clear_violation, record_open_violation

__all__ = ["main", "run"]


def _session_id() -> str:
    return os.environ.get("TRW_SESSION_ID", "")


def _break_glass(resolved: HookInput, claim: MustNotHappenClaim, reason: str) -> HookDecision | None:
    """Consume an operator token, ledger the override, and allow — or ``None``."""
    token = consume_token(resolved.root, claim_id=claim.claim_id, file_path=resolved.rel_path)
    if token is None:
        return None
    try:
        record_override(
            claim_id=claim.claim_id,
            file_path=resolved.rel_path,
            control_point="FR07",
            session_id=_session_id(),
            reason=f"break-glass token {token}: {reason}",
            block_class="break_glass",
            root=resolved.root,
        )
    except (LedgerError, OSError) as exc:
        # NFR01: if the ledger write fails, the override does NOT apply.
        return HookDecision(BLOCK, f"break-glass refused — override could not be ledgered ({type(exc).__name__})")
    clear_violation(
        resolved.root,
        claim_id=claim.claim_id,
        file_path=resolved.rel_path,
        session_id=_session_id(),
        reason=f"break-glass token {token}",
    )
    telemetry(resolved.root, "break_glass", resolved.config.telemetry_path)
    return HookDecision(ALLOW)


def _violation(resolved: HookInput, claim: MustNotHappenClaim, detail: str, *, evaluable: bool = True) -> HookDecision:
    """Fail closed on a failing OR unevaluable falsifier — but say which it was.

    ``evaluable=False`` means the check errored (collection error, missing
    dependency, timeout) rather than reporting a violation. Both block: an
    unevaluable guard is not a passing guard. They differ in what the operator
    is told and in which FR06 bucket the firing lands, because a broken check
    is an infrastructure defect, not evidence that anyone broke a claim.
    """
    override = _break_glass(resolved, claim, detail)
    if override is not None:
        return override
    label = falsifier_label(claim.falsifiers[0]) if claim.falsifiers else "<none>"
    try:
        record_violation(
            claim_id=claim.claim_id,
            file_path=resolved.rel_path,
            session_id=_session_id(),
            reason=detail,
            root=resolved.root,
        )
    except (LedgerError, OSError):
        pass  # the marker below is the enforcement; a ledger failure must not un-block
    record_open_violation(
        resolved.root,
        claim_id=claim.claim_id,
        file_path=resolved.rel_path,
        claim_text=claim.text,
        falsifier=label,
        detail=detail,
    )
    telemetry(resolved.root, "blocked" if evaluable else "infra_error", resolved.config.telemetry_path)
    if not evaluable:
        return HookDecision(
            BLOCK,
            f"INFRA_ERROR — the falsifier for {claim.claim_id} could not be evaluated.\n"
            f"  file: {resolved.rel_path}\n  falsifier: {label}\n  result: {detail}\n"
            "  This is NOT evidence the claim was violated: the check did not run.\n"
            "  Fix the check (or its dependencies), then retry.",
        )
    return HookDecision(
        BLOCK,
        f"must_not_happen violated — {claim.claim_id}: {claim.text}\n"
        f"  file: {resolved.rel_path}\n  falsifier: {label}\n  result: {detail}",
    )


def run(stream: IO[str] | None = None) -> HookDecision:
    """Evaluate one completed Write/Edit/MultiEdit against the real post-edit tree."""
    resolved = read_hook_input(stream)
    if isinstance(resolved, HookDecision):
        return resolved

    matching = enforceable_claims(resolved.contract, resolved.rel_path, resolved.root, resolved.target)
    if not matching:
        telemetry(resolved.root, "allowed_no_match", resolved.config.telemetry_path)
        return HookDecision(ALLOW)

    if resolved.escaped:
        return HookDecision(BLOCK, f"anchored target escapes the repository root: {resolved.rel_path}")

    try:
        read_bytes_nofollow(resolved.target)
    except OSError as exc:
        # The anchored target vanished or became a symlink between the tool call
        # and this check — never treat that as "nothing to enforce".
        return HookDecision(BLOCK, f"post-edit target could not be read no-follow ({type(exc).__name__})")

    for claim in matching:
        for ref in claim.falsifiers:
            result = run_falsifier(
                resolved.root,
                ref,
                timeout_seconds=resolved.config.falsifier_timeout_seconds,
                allowed_commands=tuple(resolved.config.falsifier_allowed_commands),
            )
            if not result.passed:
                return _violation(
                    resolved,
                    claim,
                    f"{result.outcome}: {result.detail}",
                    evaluable=result.outcome == "fail",
                )
        clear_violation(
            resolved.root,
            claim_id=claim.claim_id,
            file_path=resolved.rel_path,
            session_id=_session_id(),
        )

    telemetry(resolved.root, "allowed_match", resolved.config.telemetry_path)
    return HookDecision(ALLOW)


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python3 -m trw_mcp.security.intent_contract.post_edit_check``."""
    del argv
    try:
        decision = run()
    except Exception as exc:
        print(f"INTENT-CONTRACT: post-edit check failed closed ({type(exc).__name__})", file=sys.stderr)
        return BLOCK
    if decision.code != ALLOW and decision.message:
        print(f"BLOCKED (intent-contract post-edit): {decision.message}", file=sys.stderr)
    return decision.code


if __name__ == "__main__":  # pragma: no cover — CLI entry
    raise SystemExit(main())
