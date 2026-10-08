"""The one closed registry of comms refusal reasons (ledger N13).

Belongs to the ``trw_mcp.comms`` facade.

Each reason's caller-facing next action, the configuration bound it enforces,
the FR18 state it implies, whether it means "the caller did not bind as a
member", and the legacy bucket it is COUNTED under all live in one entry. Before
this, those facts were spread over six tables in the facade and in
``_guidance``, and the identity set was spelled twice.

Only static text and configuration values ever reach a caller: never exception
text or manifest-controlled data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from trw_mcp.comms._endpoints import DISPLACED_RECOVERY

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

GENERIC_DETAIL = "Peer operation refused."


@dataclass(frozen=True)
class _R:
    next_action: str
    bound: str | None = None
    state: str | None = None
    identity: bool = False
    #: FR11 refusals are COUNTED in a legacy stored bucket, so the persisted
    #: vocabulary (and every old reader of a mailbox) is unchanged; the public
    #: reason stays precise (lead board 148).
    persisted_as: str | None = None


REFUSALS: dict[str, _R] = {
    "no_pinned_run": _R("pin a run first: trw_init, or `trw-mcp run adopt` to resume one", state="no_run"),
    "no_formation": _R(
        "not a formation member: trw_inbox(action='announce') and ask the orchestrator to admit you", identity=True
    ),
    "no_matching_member": _R(
        "not a formation member: trw_inbox(action='announce') and ask the orchestrator to admit you", identity=True
    ),
    "ambiguous_member_match": _R("two members match this pin and run; ask the orchestrator to repair the manifest"),
    "stamped_identity_mismatch": _R("this run is stamped for another member; ask the orchestrator to repair it"),
    "uncanonical_formation_registration": _R("the formation index disagrees with its manifest; ask the operator"),
    "member_not_eligible": _R("this member is terminal; it can no longer use the mailbox", state="terminal"),
    "formation_unavailable": _R("the formation store is unreadable; ask the operator to repair it"),
    "worktree_record_unbound": _R(
        "this worktree is recorded for another member; ask the orchestrator to re-admit you", identity=True
    ),
    "admission_revoked": _R(
        "the orchestrator no longer admits this candidate; announce again and ask to be re-admitted",
        state="unannounced",
    ),
    "candidate_registry_full": _R("retry after candidates expire or withdraw"),
    "context_isolation_disabled": _R("enable ctx_isolation_enabled; comms needs per-connection identity"),
    "no_endpoint_for_member": _R("enroll first: trw_inbox(action='enroll')", state="joined"),
    "receiver_lease_expired": _R("renew the lease: trw_inbox(action='enroll')", state="joined"),
    "live_endpoint_held_by_other_incarnation": _R(
        "another live process holds this member; stop it or wait for it", state="joined"
    ),
    "group_closed": _R("every member is terminal; the formation mailbox is closed", state="terminal"),
    "recipient_unavailable": _R("the peer is not live; retry later or use your native channel"),
    "recipient_not_eligible": _R("the recipient is terminal or not joined; address another member"),
    "recipient_binding_mismatch": _R("the recipient changed binding; list peers and retry"),
    "invalid_recipient": _R("address a member_id from trw_inbox(action='list')"),
    "ambiguous_addressing": _R("address exactly one of recipient_member_id or scope"),
    "scope_matches_no_peer": _R("no peer declares that path; address a member directly"),
    "invalid_scope": _R("use a repo-relative path"),
    "idempotency_conflict": _R("reuse request_key only for an exact retry; choose a new key"),
    "ack_not_authorized": _R("ACK only message_ids fetched by this member"),
    "invalid_ack_ids": _R("correct the arguments and retry", bound="comms_fetch_max_items"),
    "invalid_cursor": _R("drop the cursor and fetch or list afresh"),
    "invalid_inbox_arguments": _R("correct the arguments and retry"),
    "invalid_message_enum": _R("correct the arguments and retry"),
    "invalid_request_key": _R("correct the arguments and retry"),
    "invalid_utf8": _R("send valid UTF-8 text"),
    # PRD-CORE-322 handoffs: the recipient accepts and reports, the sender completes.
    "not_a_handoff": _R("only a request is a handoff; accept, report and complete need a request's message_id"),
    "handoff_not_authorized": _R(
        "the recipient accepts and reports; only the original sender completes, and never its own handoff"
    ),
    "handoff_to_self": _R(
        "a request hands work to another member; address a peer, or checkpoint your own work",
        persisted_as="recipient_not_eligible",
    ),
    "handoff_not_accepted": _R("accept the request first: trw_inbox(action='accept')"),
    "handoff_not_reported": _R("wait for the owner's report: trw_inbox(action='status') shows it"),
    "handoff_already_reported": _R("the report is recorded; send a new request for rework"),
    "invalid_next_read": _R("next_read is 1-512 UTF-8 bytes of printable text: a branch@SHA, PRD, run path or file"),
    # PRD-CORE-349: AHR handoffs (a request offered with trw_send(handoff={"path": ...})).
    "ahr_invalid": _R(
        "the record fails AHR validation: run `trw-mcp handoff validate <file>`, fix it, seal it, resend"
    ),
    "ahr_body_conflict": _R('send an AHR with body="": the record is the content'),
    "ahr_tier_not_supported": _R("critical-tier AHRs are not accepted by this store yet; use standard or ask the lead"),
    "ahr_unaddressed_not_supported": _R("address the AHR to one member (to.id) and send it with recipient_member_id"),
    "ahr_party_mismatch": _R("the record's from.id must be you and to.id the recipient_member_id"),
    "ahr_readback_required": _R(
        "read back first: trw_inbox(action='read_back', handoff={'path': <your read-back>}) with disposition"
        " 'ready' and no contradicted claim, then accept"
    ),
    "ahr_lifecycle_refused": _R(
        "that step is not allowed in this handoff's AHR state; trw_inbox(action='status') shows it"
    ),
    "ahr_not_offered": _R("this request carries no AHR record; only a trw_send(handoff=...) request takes AHR steps"),
    "ahr_event_conflict": _R(
        "that handoff_id was already offered with other bytes; give the new record a new handoff_id"
    ),
    "ahr_ref_not_local": _R(
        "name an existing file under the project root (repo-relative path); remote refs are refused"
    ),
    "ahr_ref_unverified": _R("report next_read as <repo-relative path>#sha256:<hex of the file's current bytes>"),
    "ahr_onward_not_offered": _R(
        "offer the onward handoff first: trw_send(handoff={'path': <it>}), then report with it",
        persisted_as="ahr_lifecycle_refused",
    ),
    "ahr_expiry_exceeds_ttl": _R("set expires_at within the message TTL", bound="comms_message_ttl_seconds"),
    "ahr_report_needs_outcome": _R(
        "an AHR report needs handoff={'outcome': 'met'|'returned'|'escalated'}", persisted_as="invalid_inbox_arguments"
    ),
    "wait_disabled": _R("fetch without wait_seconds", persisted_as="invalid_inbox_arguments"),
    "wait_already_active": _R("one wait per process; fetch without wait_seconds"),
    "wait_owner_changed": _R("retry the wait under the current identity", persisted_as="invalid_inbox_arguments"),
    "wait_requires_fresh_fetch": _R(
        "wait only on a fresh fetch without a cursor", persisted_as="invalid_inbox_arguments"
    ),
    "storage_contended": _R("retry shortly"),
    "storage_publication_uncertain": _R("retry shortly"),
    "storage_unavailable": _R("the mailbox is unavailable; ask the operator"),
    "storage_corrupt": _R(
        "the mailbox failed verification; ask the operator (trw-mcp formation comms-upgrade/rollback)"
    ),
    "storage_verify_timeout": _R(
        "the comms store could not be verified within 5 s (host load can cause this); retry later,"
        " or ask the operator if it persists"
    ),
    "schema_version_mismatch": _R("restart this client on the current trw-mcp"),
    "mailbox_upgrade_required": _R("ask the operator to run trw-mcp formation comms-upgrade"),
    "upgrade_not_quiescent": _R("stop every member's comms process, then retry the upgrade"),
    "rollback_would_drop_traffic": _R("the mailbox changed since the upgrade; rollback would lose it, so keep v4"),
    "pragma_not_applied": _R("the mailbox refused its durability settings; ask the operator"),
    "response_too_small": _R("raise comms_response_max_bytes"),
    "response_body_policy_incompatible": _R("raise comms_response_max_bytes or lower comms_body_max_bytes"),
    "body_too_large": _R("send a shorter body", bound="comms_body_max_bytes"),
    "group_admission_limit": _R("wait for acknowledged messages to age out", bound="comms_group_row_limit"),
    "group_storage_budget": _R("wait for acknowledged messages to age out", bound="comms_group_body_budget_bytes"),
    "recipient_outstanding_limit": _R("wait for the recipient to ACK", bound="comms_recipient_outstanding_limit"),
    "sender_rate_limit": _R("slow down and retry", bound="comms_sender_admissions_per_minute"),
    "scope_too_broad": _R("narrow the scope or address a member", bound="comms_scope_max_recipients"),
    "invalid_wait_seconds": _R(
        "use a wait within the bound", bound="comms_wait_max_seconds", persisted_as="invalid_inbox_arguments"
    ),
    "endpoint_replaced_by_newer_incarnation": _R(DISPLACED_RECOVERY, state="joined"),
    # Pause (PAUSE-RESUME-DESIGN rev 2). Refused before any mailbox transaction, so never counted.
    "formation_paused": _R(
        "the formation is paused: ack with trw_inbox(action='ack_pause', pause_id=...) and wait for RESUME; "
        "status or reply to the orchestrator still sends",
        state="paused",
    ),
    "not_paused": _R("the formation is not paused; carry on"),
    "pause_id_mismatch": _R("ack the pause_id from your latest response; call trw_inbox to see it"),
    # Precise argument refusals; each is COUNTED under the legacy bucket so the stored vocabulary is unchanged.
    "unknown_message_id": _R(
        "no such message: the id is not in this formation's mailbox; take ids from trw_inbox fetch or status",
        persisted_as="invalid_inbox_arguments",
    ),
    "invalid_message_id": _R(
        "message id must be 32 hex characters, as returned in a send receipt", persisted_as="invalid_inbox_arguments"
    ),
    "report_needs_next_read": _R(
        "report needs next_read: where the sender should look (branch@SHA, PRD, run path or file)",
        persisted_as="invalid_inbox_arguments",
    ),
    "report_takes_one_message_id": _R(
        "report takes exactly one message id; report each handoff in its own call",
        persisted_as="invalid_inbox_arguments",
    ),
    "invalid_message_body": _R("send a non-blank body", persisted_as="invalid_inbox_arguments"),
}
IDENTITY_REASONS = frozenset(reason for reason, spec in REFUSALS.items() if spec.identity)


def detail(reason: str, config: TRWConfig | None = None) -> str:
    """The next action for *reason*, with the enforced bound's value when *config* is given."""
    spec = REFUSALS.get(reason)
    if spec is None:
        return GENERIC_DETAIL
    if spec.bound is not None and config is not None:
        return f"{spec.next_action} (bound {spec.bound}={getattr(config, spec.bound)})"
    return spec.next_action


def persisted_bucket(reason: str) -> str:
    """The stored refusal counter for *reason*; unknown reasons still fail closed in count_refusal."""
    spec = REFUSALS.get(reason)
    return spec.persisted_as if spec is not None and spec.persisted_as else reason


def implied_state(reason: str) -> str | None:
    spec = REFUSALS.get(reason)
    return spec.state if spec is not None else None


__all__ = ["GENERIC_DETAIL", "IDENTITY_REASONS", "REFUSALS", "detail", "implied_state", "persisted_bucket"]
