"""Admission records for the PRD-CORE-274 comms fields.

Its own table module rather than ten more entries in
``_field_admission_registry.py``: that file sits close to the 350 effective-LOC
gate, so every domain that admits fields brings its own table and the registry
merges it (the pattern PRD-FIX-123 established).

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.

Every default here is a CONSERVATIVE POLICY CHOICE, not a measured optimum. The
PRD states this explicitly and these records repeat it, because a number that
survives long enough in a config table starts being cited as a benchmark.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_comms_a02 import AMENDMENT_02_ADMISSIONS
from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_PRD = "docs/requirements-aare-f/prds/PRD-CORE-274-cross-harness-peer-messaging-slice-1.md"
_SURFACE_TEST = "trw-mcp/tests/comms/test_surface_config.py"

COMMS_ADMISSIONS: dict[str, ConfigAdmission] = {
    "comms_enabled": ConfigAdmission(
        field_name="comms_enabled",
        owner="PRD-CORE-274-NFR07",
        consumer="trw_mcp.tools.swarm_comms -> trw_mcp.comms facade",
        default_rationale=(
            "true (Amendment 02, NFR07). The tools are inert without a formation: identity "
            "binding (FR01) refuses before any group row or database exists, so enabling creates "
            "no state on a project that never forms a formation, and membership still needs the "
            "orchestrator's explicit manifest. An explicit false at the highest-precedence layer "
            "of the existing env > project > machine cascade hides the tools."
        ),
        interaction_analysis=(
            "Checked before any comms path touches disk, so it gates creation rather than use; "
            "disabling later RETAINS existing state rather than deleting it, and the PRD forbids "
            "representing that as a safe reset. It does not interact with the identity binding: "
            "a caller that cannot bind is refused whether or not comms is enabled."
        ),
        deprecation_plan=(
            "Retain until the substrate has production traces. Removing it would make the only "
            "way to stop comms a downgrade."
        ),
        docs_pointer=_PRD,
        test_pointer=f"{_SURFACE_TEST}::test_disabled_by_default_creates_no_comms_state",
        budget_decision="admitted",
    ),
    "comms_group_row_limit": ConfigAdmission(
        field_name="comms_group_row_limit",
        owner="PRD-CORE-274-FR15",
        consumer="trw_mcp.comms._store admission transaction",
        default_rationale=(
            "4096, bounded 1..4096. A lifetime ceiling on one group's retained rows (tombstones "
            "included), so a runaway loop terminates by construction. The maximum is the NFR08 "
            "envelope, measured on the development Mac (an environment observation)."
        ),
        interaction_analysis=(
            "Snapshotted per group at first use, so changing it affects new groups only — an "
            "operator cannot raise the ceiling to rescue a group that has already exhausted it. "
            "Exhaustion is a typed refusal inside the single admission transaction, so no partial "
            "charge is recorded against the sender's rate budget."
        ),
        deprecation_plan="Retain; removal reinstates unbounded durable growth per group.",
        docs_pointer=_PRD,
        test_pointer="trw-mcp/tests/comms/test_admission_transaction.py",
        budget_decision="admitted",
    ),
    "comms_body_max_bytes": ConfigAdmission(
        field_name="comms_body_max_bytes",
        owner="PRD-CORE-274-FR03",
        consumer="trw_mcp.comms._envelope body validation",
        default_rationale=(
            "8192 UTF-8 BYTES, bounded 1..65536. Byte-denominated rather than character-"
            "denominated because the limits it feeds — storage and response size — are bytes, and "
            "a character limit would let a multi-byte body exceed a byte ceiling silently."
        ),
        interaction_analysis=(
            "Snapshotted per group with the other admission limits. Bound together with "
            "comms_response_max_bytes by a model validator that REFUSES incompatible pairs: a "
            "maximally escaped body expands sixfold in canonical JSON, so an unchecked pair "
            "admits messages that can never be fetched back."
        ),
        deprecation_plan="Retain; it is the input bound the response bound is derived against.",
        docs_pointer=_PRD,
        test_pointer=f"{_SURFACE_TEST}::test_response_bound_must_cover_escaped_body",
        budget_decision="admitted",
    ),
    "comms_recipient_outstanding_limit": ConfigAdmission(
        field_name="comms_recipient_outstanding_limit",
        owner="PRD-CORE-274-FR03",
        consumer="trw_mcp.comms._store admission transaction",
        default_rationale=(
            "64, bounded 1..256. The back-pressure knob: a peer that stops fetching stops being "
            "sendable to. That is deliberately visible — senders are refused — rather than "
            "absorbed into a queue that grows behind a silent peer."
        ),
        interaction_analysis=(
            "Counted per recipient inside the admission transaction, so it refuses before any row "
            "is written. Independent of the group lifetime ceiling: a group can be far from its "
            "limit while one recipient is saturated, which is the intended asymmetry."
        ),
        deprecation_plan="Retain; removal makes a dead peer look healthy to every sender.",
        docs_pointer=_PRD,
        test_pointer="trw-mcp/tests/comms/test_admission_transaction.py",
        budget_decision="admitted",
    ),
    "comms_fetch_max_items": ConfigAdmission(
        field_name="comms_fetch_max_items",
        owner="PRD-CORE-274-FR04",
        consumer="trw_mcp.comms._store bounded fetch page",
        default_rationale=(
            "16, bounded 1..64. Fetch is a PAGE, never a drain: messages stay fetchable until "
            "explicitly ACKed, so a lost response costs a repeat page rather than a lost message."
        ),
        interaction_analysis=(
            "Bounds the page before serialization; the page is then packed against "
            "comms_response_max_bytes including metadata and continuation, so the byte ceiling "
            "can return fewer items than this field allows. It never causes a destructive "
            "dequeue, so lowering it cannot lose a message."
        ),
        deprecation_plan="Retain as the page bound; removal reinstates an unbounded read.",
        docs_pointer=_PRD,
        test_pointer="trw-mcp/tests/comms/test_fetch_ack.py",
        budget_decision="admitted",
    ),
    "comms_response_max_bytes": ConfigAdmission(
        field_name="comms_response_max_bytes",
        owner="PRD-CORE-274-NFR06",
        consumer="trw_mcp.tools.swarm_comms response serialization",
        default_rationale=(
            "65536 bytes of canonical JSON tool payload, excluding host MCP framing, bounded "
            "4096..262144. Tool responses are paid by the calling LLM on every call, so the "
            "ceiling is explicit rather than emergent."
        ),
        interaction_analysis=(
            "Must satisfy >= 6 * comms_body_max_bytes + 4096 or the configuration is REFUSED, not "
            "clamped: clamping would leave an operator running a system whose stated limits are "
            "not its real ones, first observed as an admitted message that can never be read. "
            "Empty and error responses are held to the same ceiling."
        ),
        deprecation_plan="Retain; it is the caller-facing token cost bound.",
        docs_pointer=_PRD,
        test_pointer=f"{_SURFACE_TEST}::test_response_bound_must_cover_escaped_body",
        budget_decision="admitted",
    ),
    "comms_sender_admissions_per_minute": ConfigAdmission(
        field_name="comms_sender_admissions_per_minute",
        owner="PRD-CORE-274-FR09",
        consumer="trw_mcp.comms._store rate accounting",
        default_rationale=(
            "32 per 60 seconds, bounded 1..256. Counted from PERSISTED commit timestamps, so a "
            "restart does not replenish the budget and a crash-loop cannot outrun it."
        ),
        interaction_analysis=(
            "Effective time is max(wall clock, last persisted group time), so moving the system "
            "clock backwards cannot replenish rate or leases. Exact idempotent retries bypass "
            "charging entirely — they are the same admission, not a new one — which is why a "
            "retry storm cannot exhaust this budget."
        ),
        deprecation_plan="Retain; it is the per-sender half of the overhead ceiling.",
        docs_pointer=_PRD,
        test_pointer="trw-mcp/tests/comms/test_idempotent_post.py",
        budget_decision="admitted",
    ),
    "comms_poll_interval_seconds": ConfigAdmission(
        field_name="comms_poll_interval_seconds",
        owner="PRD-CORE-274-FR06",
        consumer="trw_mcp.tools.swarm_comms participation reporting",
        default_rationale=(
            "15 seconds, floor 15, ceiling 3600. The floor is deliberate: this substrate is "
            "PULL-ONLY, every poll costs the caller a turn, and a tighter interval buys latency "
            "that the turn cost does not justify. It is advertised, never enforced on a peer."
        ),
        interaction_analysis=(
            "Advisory to callers and an input to the lease floor: comms_lease_ttl_seconds must be "
            "at least twice this value or the configuration is refused, so raising the interval "
            "without raising the lease cannot fence a peer that is polling correctly."
        ),
        deprecation_plan=(
            "Retain until a native wake path exists for a peer; at that point this becomes the "
            "fallback interval rather than the only mechanism."
        ),
        docs_pointer=_PRD,
        test_pointer="trw-mcp/tests/comms/test_participation_modes.py",
        budget_decision="admitted",
    ),
    "comms_lease_ttl_seconds": ConfigAdmission(
        field_name="comms_lease_ttl_seconds",
        owner="PRD-CORE-274-FR07",
        consumer="trw_mcp.comms._endpoints lease and incarnation fencing",
        default_rationale=(
            "120 seconds, bounded 1..86400 and additionally at least twice the poll interval. "
            "Long enough that a peer doing real work between polls keeps its endpoint, short "
            "enough that a vanished peer stops being a valid target promptly."
        ),
        interaction_analysis=(
            "Expiry fences an OLD INCARNATION: a stale process cannot ACK or be targeted after "
            "its lease lapses, which is what stops a respawned peer inheriting messages it never "
            "saw. Shares the backwards-clock protection with rate accounting, so a clock rollback "
            "cannot extend a lease. The cross-field floor is enforced at config load, not clamped."
        ),
        deprecation_plan="Retain; without a lease a dead peer is indistinguishable from a slow one.",
        docs_pointer=_PRD,
        test_pointer="trw-mcp/tests/comms/test_dead_peer.py",
        budget_decision="admitted",
    ),
    "comms_sqlite_busy_timeout_ms": ConfigAdmission(
        field_name="comms_sqlite_busy_timeout_ms",
        owner="PRD-CORE-274-FR03",
        consumer="trw_mcp.comms._store SQLite connection setup",
        default_rationale=(
            "5000 ms, bounded 1..30000. A BOUNDED wait, so contention from concurrent harnesses "
            "reports a refusal instead of feeling like a hang. Expiry REFUSES; it never writes "
            "outside the transaction."
        ),
        interaction_analysis=(
            "Applies to the single immediate admission transaction, which is what makes the "
            "correctness of admission independent of advisory file locking — load-bearing here "
            "because the repo's advisory lock helper no-ops on Windows. Raising it trades latency "
            "for fewer refusals under contention; it cannot affect what a committed row contains."
        ),
        deprecation_plan="Retain; an unbounded busy wait wedges a harness behind a stuck writer.",
        docs_pointer=_PRD,
        test_pointer="trw-mcp/tests/comms/test_admission_transaction.py",
        budget_decision="admitted",
    ),
    "comms_scope_max_recipients": ConfigAdmission(
        field_name="comms_scope_max_recipients",
        owner="PRD-CORE-276-FR03",
        consumer="trw_mcp.comms scoped notify fan-out",
        default_rationale=(
            "4, bounded 1..32. The ceiling that keeps targeted broadcast from becoming broadcast. "
            "Four is a conservative policy choice, not a measured optimum: it is roughly the size "
            "of a working formation, and the first real multi-member transcript is what should "
            "move it."
        ),
        interaction_analysis=(
            "Checked against the RESOLVED recipient set inside the operation transaction and "
            "before the first insert, so exceeding it costs a refusal rather than a partial "
            "fan-out. It narrows addressing only; every admitted message is still charged against "
            "the group lifetime cap, the recipient outstanding cap and the sender rate limit, so "
            "raising this cannot raise the message budget."
        ),
        deprecation_plan=(
            "Retain. Removing it makes a scope with a broad glob reach every member, which is the "
            "overhead explosion the design exists to prevent."
        ),
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-276-scoped-notify-targeted-broadcast.md",
        test_pointer="trw-mcp/tests/comms/test_scoped_notify.py",
        budget_decision="admitted",
    ),
    "comms_scope_max_bytes": ConfigAdmission(
        field_name="comms_scope_max_bytes",
        owner="PRD-CORE-276-FR02",
        consumer="trw_mcp.comms._scope parser",
        default_rationale=(
            "1024 bytes, bounded 1..4096. A scope is a repo-relative path; longer than this is a "
            "mistake rather than a deep tree. Matches the plan package's path bound so the two "
            "addressing surfaces refuse the same oversized input."
        ),
        interaction_analysis=(
            "Applied before any matching, so an oversized scope costs one parse rather than a "
            "walk over every member's declarations. Independent of the body bound: a scope is an "
            "address, not content."
        ),
        deprecation_plan="Retain; an unbounded address is unbounded work per member.",
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-276-scoped-notify-targeted-broadcast.md",
        test_pointer="trw-mcp/tests/comms/test_scoped_notify.py",
        budget_decision="admitted",
    ),
    "comms_wait_max_seconds": ConfigAdmission(
        field_name="comms_wait_max_seconds",
        owner="PRD-CORE-274-FR11",
        consumer="trw_mcp.comms inbox bounded wait admission",
        default_rationale=(
            "30 seconds, bounded 0..300; 0 refuses every positive wait (the kill switch). A "
            "conservative policy choice, not a measured optimum. The cap bounds only how long a "
            "caller may ASK to wait; it promises nothing about the remaining lease, and observed "
            "latency to a message is unmeasured and depends on SQLite contention and the sleep "
            "interval."
        ),
        interaction_analysis=(
            "Per-call opt-in: wait_seconds defaults to 0, so existing callers are byte-identical. "
            "Deliberately NOT validated against comms_lease_ttl_seconds — that would reject an "
            "existing valid poll-15/lease-30 configuration and could not bound the REMAINING lease; "
            "each attempt re-verifies lease and incarnation instead. Re-read from the effective "
            "runtime config on every attempt, so lowering it to 0 ends an in-flight wait."
        ),
        deprecation_plan=(
            "Retain until a native wake path exists; then this bounds the fallback retry, not the only mechanism."
        ),
        docs_pointer=_PRD,
        test_pointer=_SURFACE_TEST,
        budget_decision="admitted",
    ),
    "comms_wait_interval_ms": ConfigAdmission(
        field_name="comms_wait_interval_ms",
        owner="PRD-CORE-274-FR11",
        consumer="trw_mcp.comms inbox bounded wait sleep between attempts",
        default_rationale=(
            "1000 ms, bounded 100..15000. A policy choice, not a measured optimum. Each attempt is a "
            "write transaction (group clock touch plus a bounded read) whose cost under contention "
            "is unmeasured; a shorter interval trades mailbox load for latency, and the floor stops "
            "a caller turning the wait into a busy loop."
        ),
        interaction_analysis=(
            "A server-internal re-read inside the enrolled process, distinct from "
            "comms_poll_interval_seconds, which still governs how often a CLIENT should call. "
            "Also the cancellation granularity while sleeping: a cancel or disconnect is noticed at "
            "the next boundary, which is at most one interval away when the loop is asleep."
        ),
        deprecation_plan="Retain with the wait cap; meaningless without it.",
        docs_pointer=_PRD,
        test_pointer=_SURFACE_TEST,
        budget_decision="admitted",
    ),
}

COMMS_ADMISSIONS.update(AMENDMENT_02_ADMISSIONS)

__all__ = ["COMMS_ADMISSIONS"]
