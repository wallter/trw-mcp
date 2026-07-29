"""Canonical no-active-run reason marker and remedy set (PRD-CORE-233 FR03).

Belongs to the ``state/_paths.py`` run-resolution surface. It lives in its own
leaf so the resolver's raise site and the shared ceremony hint builder
(``tools/_ceremony_runtime_helpers._no_active_run_hint``) render the SAME
remedies without importing each other — FR03 requires that a caller never gets
different options for the same condition depending on entry point.

Why the ordering matters: the first remedy is the ONLY one a delegated
sub-agent can execute. Seven bundled agents grant ``trw_checkpoint`` without
``trw_init`` or ``trw_adopt_run``; the pre-FR03 message named only those two
tools, so the printed fix was unexecutable for exactly the callers who hit it.
``run_path=`` needs no additional grant — ``resolve_run_path`` short-circuits
on an explicit in-project path before any pin lookup.
"""

from __future__ import annotations

from trw_mcp.exceptions import StateError

#: Machine-readable marker carried in the ``StateError`` context of the
#: ctx-aware no-pin refusal. Consumers (FR02's checkpoint softening) key on
#: this instead of the message text, so wording changes stay safe.
NO_ACTIVE_RUN_REASON = "no_active_run_for_session"

#: Remedies, most-executable-first. Rendered verbatim into every no-run
#: message site; a test asserts both sites contain every entry.
NO_ACTIVE_RUN_REMEDIES: tuple[str, ...] = (
    "pass run_path=<run directory> to this call",
    "call trw_init() to create a run",
    "call trw_adopt_run(run_path=...) to resume one",
)


def no_active_run_remedy() -> str:
    """One-line remedy sentence shared by every no-active-run message site."""
    return "Remedy: " + ", or ".join(NO_ACTIVE_RUN_REMEDIES) + "."


def is_no_active_run(exc: StateError) -> bool:
    """True only for the ctx-aware no-pin refusal, not for other state faults.

    Used by :func:`trw_mcp.tools._orchestration_checkpoint.execute_checkpoint`
    to soften exactly one branch: a missing precondition. A bad ``run_path``
    (absent, or escaping the project root) is a caller mistake and must keep
    raising, so this classifier must never widen to "any StateError".
    """
    return exc.context.get("reason") == NO_ACTIVE_RUN_REASON
