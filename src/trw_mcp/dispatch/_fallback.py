"""Ordered client fallback for a dispatch that hit an exhausted quota (7.0.0 W21).

Belongs to the ``trw_mcp.dispatch`` package; the ``trw-mcp dispatch`` CLI calls it.

During the 6.0.0 release the codex, grok and agy reviewer quotas were all out for
2h45m, a wrapper read codex's usage-limit refusal as a review still running
(L-rW01), and the same-vendor stand-in reviews missed P0s (L-qJmp).
:func:`dispatch_with_fallback` owns the one attempt loop: it moves to the next
listed client ONLY when a child refused on quota or never ran, records every
attempt, and names the client whose result it returns.

Any other failure -- a timeout, a stop, an empty answer -- ends the chain and is
reported as-is: letting another client answer would hide a real failure behind a
different agent's output. Nothing is added implicitly: the chain is exactly the
operator's list, so the host's own client runs only when listed, and a verdict
from it is flagged in ``fallback_note``.

A listed client that cannot run the first request's posture is skipped as
``posture_unsupported`` before anything is built or launched: a bounded review
never falls back to an unbounded child. Today only claude and codex carry a
reviewer argv, so a reviewer chain of codex then grok or agy has no second vendor.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import structlog

from trw_mcp.dispatch._client_specs import CLIENT_SPECS
from trw_mcp.dispatch._posture import ReviewerPostureError, verify_reviewer_posture
from trw_mcp.dispatch._resolve import DispatchResolutionError
from trw_mcp.dispatch._runner import dispatch
from trw_mcp.dispatch._types import DispatchAttempt, DispatchRequest, DispatchResult
from trw_mcp.state.source_detection import detect_client_profile

__all__ = ["dispatch_with_fallback", "host_dispatch_client"]

logger = structlog.get_logger(__name__)

#: silence_reasons after which no child did the work, so another client may.
_FAIL_OVER_REASONS = frozenset({"quota_exhausted", "client_unsupported", "sandbox_unsupported"})
#: The runner's exit code for a binary that could not be launched.
_LAUNCH_FAILED_EXIT = -127
#: Attempts that were skipped without running, so no child answered.
_SKIPPED_REASONS = frozenset({"unresolved", "posture_unsupported"})
#: Attempt reasons that hand over to the next client.
_NEXT_CLIENT_REASONS = _FAIL_OVER_REASONS | _SKIPPED_REASONS | {"launch_failed"}


def host_dispatch_client() -> str | None:
    """The dispatch client id of the harness running this process, or None when unknown."""
    profile = detect_client_profile()
    return next((cid for cid, spec in CLIENT_SPECS.items() if profile and spec.profile_id == profile), None)


def _fail_over_reason(result: DispatchResult) -> str | None:
    """Why *result* lets the next client run, or None when it is the answer (good or bad)."""
    if result.exit_code == _LAUNCH_FAILED_EXIT:
        return "launch_failed"
    return result.silence_reason if result.silence_reason in _FAIL_OVER_REASONS else None


def dispatch_with_fallback(
    first: DispatchRequest,
    fallback_clients: Sequence[str],
    build: Callable[[str], DispatchRequest],
    *,
    run: Callable[[DispatchRequest], DispatchResult] = dispatch,
    host_client: str | None = None,
) -> DispatchResult:
    """Run *first*, then each listed client in order while the previous one failed over.

    *build* turns a client id into its request (the caller's own resolver, so a
    fallback gets the same role, posture and config precedence); a client it
    refuses is recorded ``unresolved`` and skipped. A client that cannot run
    *first*'s posture is recorded ``posture_unsupported`` and never built or run.
    Duplicates and the primary are dropped from *fallback_clients*. With no
    fallbacks the result is untouched.
    """
    chain = list(dict.fromkeys(c for c in fallback_clients if c and c != first.client))
    result = run(first)
    if not chain:
        return result
    attempts = [DispatchAttempt(client=result.client, reason=_fail_over_reason(result) or result.silence_reason)]
    for client in chain:
        if attempts[-1].reason not in _NEXT_CLIENT_REASONS:
            break
        logger.warning("dispatch_fallback_next", failed=attempts[-1].client, reason=attempts[-1].reason, next=client)
        try:
            verify_reviewer_posture(client, first.posture, read_only=first.read_only)
        except ReviewerPostureError as exc:
            attempts.append(DispatchAttempt(client=client, reason="posture_unsupported"))
            logger.warning(
                "dispatch_fallback_posture_unsupported", client=client, posture=first.posture, error=str(exc)
            )
            continue
        try:
            request = build(client)
        except DispatchResolutionError as exc:
            attempts.append(DispatchAttempt(client=client, reason="unresolved"))
            logger.warning("dispatch_fallback_unresolved", client=client, error=str(exc))
            continue
        result = run(request)
        attempts.append(
            DispatchAttempt(client=result.client, reason=_fail_over_reason(result) or result.silence_reason)
        )
    return result.model_copy(update={"attempts": attempts, "fallback_note": _note(attempts, result, host_client)})


def _note(attempts: list[DispatchAttempt], result: DispatchResult, host_client: str | None) -> str:
    """Say which client answered and why earlier ones did not; empty when the first one answered."""
    tried = ", ".join(f"{a.client}={a.reason}" for a in attempts)
    if _fail_over_reason(result) is not None or attempts[-1].reason in _SKIPPED_REASONS:
        return f"no verdict: all {len(attempts)} clients failed over ({tried})"
    if len(attempts) == 1:
        return ""
    note = f"result from fallback client {result.client}; attempts: {tried}"
    if host_client is not None and result.client == host_client:
        note += "; this is the same client as the host, so the review is not cross-vendor"
    return note
