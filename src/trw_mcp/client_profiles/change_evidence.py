"""Which client profiles have a hook that WRITES change evidence (E2E-INC-115 b).

The unpinned deliver gate decides "did this session change files?" from ``file_modified`` records that a client hook
writes (``data/hooks/post-tool-event.sh``). A client with no such hook writes nothing, and an empty record set reads
as "nothing changed" -- so ``trw_deliver`` passed with no build on a session that had edited code. Absence of evidence
is only meaningful when a writer exists to produce it; without one the change count is UNCOMPUTABLE, never zero.

A registry, like ``session_identity``: a profile joins ``CHANGE_EVIDENCE_WRITERS`` in the same change that makes its
hooks write the records (Cursor and Codex hooks are being taught to). Nothing else about a profile is inferred.
"""

from __future__ import annotations

# DEFAULT-DENY: a profile is covered only by being listed. cursor-ide and codex stay OFF even once their hooks write
# records, because both can also edit through a shell that no hook sees; they count as covered only when the per-session
# snapshot witness is unioned with the hook records (decided with its owner then).
CHANGE_EVIDENCE_WRITERS: frozenset[str] = frozenset({"claude-code"})


def active_client_writes_change_evidence() -> bool:
    """True when the active client profile has a registered change-evidence writer.

    The profile is the explicit ``TRW_CLIENT_PROFILE``, else the environment signals ``detect_client_profile``
    reads; an unidentifiable client has no writer.
    """
    try:
        from trw_mcp.state.source_detection import detect_client_profile
        from trw_mcp.tools._client_detection import resolve_client_profile

        profile = resolve_client_profile()
        if profile == "unknown":
            profile = detect_client_profile() or profile
    except Exception:  # justified: fail-CLOSED, a client that cannot be identified has no writer
        return False  # trw-fail-silent-allow: False is the closed direction; the caller then reports the gap
    return profile in CHANGE_EVIDENCE_WRITERS


def change_evidence_gap_reason() -> str:
    """The named reason a client without a registered writer cannot show a session was code-free."""
    return (
        "this client has no hook registered to record file changes, so the session cannot be shown to be code-free; "
        "pin a run with trw_init() / `trw-mcp run adopt`, or run project-native validation and record it with "
        "trw_build_check()"
    )
