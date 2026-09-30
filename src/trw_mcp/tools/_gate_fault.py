"""The verdict a blocking deliver gate returns when its own machinery faults.

CONSTITUTION 1.a: a code bug must never become a pass on a blocking gate. A gate that catches an unexpected
exception returns this text as its blocking warning instead of "nothing to report". It names the exception TYPE and the
gate SITE and never the message (a message can carry a path or a credential); the documented escape is offered.
Callers set the verdict from this constant FIRST and log the diagnostics afterwards, so a failing logger cannot
turn the block back into a pass.
"""

from __future__ import annotations

_BUILD_REMEDY = (
    "Run project-native validation and record it with trw_build_check(), or override with "
    "allow_unverified=true + an unexpired acceptable-failure record."
)


def gate_fault_block(site: str, error: BaseException, remedy: str = _BUILD_REMEDY) -> str:
    """The blocking text for a fault of type ``type(error)`` inside the *site* gate.

    *remedy* must be a way out the gate really honours: the review-scope gate has no ``allow_unverified`` escape.
    """
    return (
        f"Delivery blocked: the {site} gate faulted ({type(error).__name__}), so it cannot vouch for this delivery. "
        f"{remedy} The fault is in the server log."
    )
