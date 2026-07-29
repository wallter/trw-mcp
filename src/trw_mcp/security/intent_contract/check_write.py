"""FR05: the PreToolUse pre-write check — METADATA ONLY, never a falsifier.

A pre-write hook structurally cannot see the post-edit state, so running a
falsifier here validates the wrong tree (codex #6): the still-safe current file
passes and the violating edit is then applied unchecked. This entry point
therefore does path/anchor/authority/state metadata work only and defers the
substantive check to FR07, which reads the ACTUAL written file.

It blocks on exactly two conditions plus one reserved extension point:
malformed/unloadable contract, stale enrollment, and a claim whose policy
requires pre-write gating (no v0 claim does — see :func:`requires_pre_write_gate`).

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

import sys
from typing import IO

from trw_mcp.security.intent_contract._anchors import enforceable_claims
from trw_mcp.security.intent_contract._hook_common import (
    ALLOW,
    BLOCK,
    HookDecision,
    read_hook_input,
    telemetry,
)
from trw_mcp.security.intent_contract._models import MustNotHappenClaim

__all__ = ["main", "requires_pre_write_gate", "run"]


def requires_pre_write_gate(claim: MustNotHappenClaim) -> bool:
    """Reserved extension point: no v0 claim requires a pre-write block.

    The binding subset carries no per-claim pre-write policy field, so this is
    False for every loadable claim. It exists so a future schema extension has
    one place to land instead of re-deriving the gate at the hook layer.
    """
    del claim
    return False


def run(stream: IO[str] | None = None) -> HookDecision:
    """Evaluate one PreToolUse payload. Never spawns a falsifier subprocess."""
    resolved = read_hook_input(stream)
    if isinstance(resolved, HookDecision):
        return resolved

    matching = enforceable_claims(resolved.contract, resolved.rel_path, resolved.root, resolved.target)
    if not matching:
        telemetry(resolved.root, "allowed_no_match", resolved.config.telemetry_path)
        return HookDecision(ALLOW)

    if resolved.escaped:
        # An anchored path that symlink-resolves out of the repo would make the
        # checked path differ from the eventual write target (R14).
        return HookDecision(BLOCK, f"anchored target escapes the repository root: {resolved.rel_path}")

    gated = [claim for claim in matching if requires_pre_write_gate(claim)]
    telemetry(resolved.root, "allowed_match", resolved.config.telemetry_path)
    if gated:  # pragma: no cover — unreachable until the schema grows the field
        return HookDecision(BLOCK, f"pre-write gating required for: {', '.join(c.claim_id for c in gated)}")
    return HookDecision(ALLOW)


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python3 -m trw_mcp.security.intent_contract.check_write``."""
    del argv
    try:
        decision = run()
    except Exception as exc:
        print(f"INTENT-CONTRACT: pre-write check failed closed ({type(exc).__name__})", file=sys.stderr)
        return BLOCK
    if decision.code != ALLOW and decision.message:
        print(f"BLOCKED (intent-contract pre-write): {decision.message}", file=sys.stderr)
    return decision.code


if __name__ == "__main__":  # pragma: no cover — CLI entry
    raise SystemExit(main())
