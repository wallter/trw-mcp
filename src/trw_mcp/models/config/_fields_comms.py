"""Cross-harness comms config fields (PRD-CORE-274 NFR06, NFR07).

Typed, bounded knobs. ``comms_enabled`` is the execution kill switch and
defaults to **true** (Amendment 02, NFR07): with no formation the tools create no
state -- identity binding refuses before any database or group row exists -- and
an explicit false at the highest-precedence layer that sets it hides them.

The numbers are conservative policy choices, NOT measured optima, and the PRD
says so; nothing here should be cited as a benchmark. They live in config rather
than as literals so an operator can retune a running swarm without a redeploy.

Two constraints are cross-field and REJECT the configuration rather than being
clamped, because silently repairing either one produces a system that loses
messages while reporting success:

- ``comms_response_max_bytes >= 6 * comms_body_max_bytes + 4096`` — a body of
  maximally-escaped UTF-8 expands up to sixfold in canonical JSON, so a response
  ceiling below that bound makes a legally-admitted message permanently
  unfetchable. The 4096 is the minimum envelope of metadata and continuation.
- ``comms_lease_ttl_seconds >= 2 * comms_poll_interval_seconds`` — a lease that
  expires within one polling period fences a peer that is behaving correctly.
"""

from __future__ import annotations

from pydantic import Field, model_validator

# ``typing.Self`` is 3.11+ and this package supports 3.10; typing_extensions is
# already a declared dependency, so this adds nothing to the install.
from typing_extensions import Self

#: Worst-case growth of one UTF-8 byte under canonical JSON string escaping
#: (``\u00XX``), and the envelope floor for metadata plus continuation. Named
#: because the response/body relation below is unreadable as bare integers.
_JSON_ESCAPE_WORST_CASE = 6
_RESPONSE_ENVELOPE_FLOOR_BYTES = 4096

#: A lease must outlive at least this many polling periods.
_MIN_LEASE_POLL_PERIODS = 2


class _CommsFields:
    """Comms domain mixin — mixed into _TRWConfigFields via MI."""

    #: Execution kill switch. Default **true** (NFR07): inert without a formation,
    #: because FR01 binding refuses before any comms state exists. An explicit
    #: false in the effective config cascade (env > project > machine) wins.
    comms_enabled: bool = True

    #: Rows one group may retain over its lifetime, tombstones included (PRD-CORE-274
    #: FR15). The maximum is the NFR08 envelope: whole-mailbox verification under the
    #: write lock was measured within the lock-hold target at 4096 rows and not beyond.
    comms_group_row_limit: int = Field(
        default=4096, ge=1, le=4096, description="Lifetime rows (messages incl. tombstones) one comms group may hold."
    )

    #: Live (non-tombstoned) body bytes one group may hold (FR15), capped by the NFR08
    #: envelope measured at full body size.
    comms_group_body_budget_bytes: int = Field(
        default=16777216, ge=65536, le=16777216, description="Live message-body bytes one comms group may hold."
    )

    #: After a terminal row's deadline, how long its body stays before tombstoning (FR15).
    comms_retry_grace_seconds: int = Field(
        default=3600, ge=0, le=86400, description="Seconds a terminal message body is kept past its deadline."
    )

    #: Message body ceiling in UTF-8 BYTES, not characters — the limit exists to
    #: bound storage and response size, and both are byte-denominated.
    comms_body_max_bytes: int = Field(
        default=8192, ge=1, le=65536, description="Maximum comms message body size, in UTF-8 bytes."
    )

    #: Unacknowledged messages one recipient may hold. The back-pressure knob: a
    #: peer that stops fetching stops being sendable to, rather than
    #: accumulating an unbounded queue.
    comms_recipient_outstanding_limit: int = Field(
        default=64, ge=1, le=256, description="Maximum unacknowledged messages outstanding for one recipient."
    )

    #: Messages returned by one bounded fetch. Fetch is a page, never a drain.
    comms_fetch_max_items: int = Field(
        default=16, ge=1, le=64, description="Maximum messages returned by a single comms fetch page."
    )

    #: Ceiling on the canonical JSON tool payload, excluding host MCP framing.
    #: Empty and error responses must fit it too.
    comms_response_max_bytes: int = Field(
        default=65536,
        ge=4096,
        le=262144,
        description="Maximum canonical JSON comms tool response, in UTF-8 bytes, excluding MCP framing.",
    )

    #: Committed admissions allowed per sender in the preceding 60 seconds.
    #: Counted from persisted timestamps, so a restart does not replenish it.
    comms_sender_admissions_per_minute: int = Field(
        default=32, ge=1, le=256, description="Committed admissions allowed per sender per 60 seconds."
    )

    #: Advertised pull interval. The floor is 15s deliberately: this substrate is
    #: pull-only, and a tighter interval buys latency that the turn cost of a
    #: poll does not justify.
    comms_poll_interval_seconds: int = Field(
        default=15, ge=15, le=3600, description="Advertised comms poll interval, in seconds."
    )

    #: Endpoint lease lifetime. Floor is enforced relative to the poll interval
    #: by the validator below, not by this bound alone.
    comms_lease_ttl_seconds: int = Field(
        default=120, ge=1, le=86400, description="Comms endpoint lease lifetime, in seconds."
    )

    #: Peers one scoped notify may reach. The ceiling that keeps "targeted
    #: broadcast" from becoming broadcast: a resolved recipient set larger than
    #: this REFUSES before a single row is written, rather than fanning out and
    #: relying on the per-message limits to absorb it afterwards.
    comms_scope_max_recipients: int = Field(
        default=4, ge=1, le=32, description="Maximum peers one scoped comms notify may reach."
    )

    #: Scope path ceiling in UTF-8 bytes. A scope is a repo-relative path, and a
    #: path longer than this is a mistake, not a deep tree.
    comms_scope_max_bytes: int = Field(
        default=1024, ge=1, le=4096, description="Maximum comms scope path length, in UTF-8 bytes."
    )

    #: Bounded SQLite busy wait. A timeout REFUSES; it never writes unlocked.
    comms_sqlite_busy_timeout_ms: int = Field(
        default=5000, ge=1, le=30000, description="Bounded SQLite busy timeout for comms transactions, in ms."
    )

    #: Ceiling for ``trw_inbox(wait_seconds=...)`` (PRD-CORE-274 Amendment 01,
    #: FR11). 0 is the kill switch: every positive wait refuses ``wait_disabled``.
    #: Deliberately NOT tied to the lease by a validator — a wait cannot promise
    #: remaining lease anyway; each attempt re-checks the lease itself.
    comms_wait_max_seconds: int = Field(
        default=30, ge=0, le=300, description="Maximum bounded inbox wait a caller may request, in seconds; 0 disables."
    )

    #: Sleep between attempts of a bounded wait. A server-internal re-read inside
    #: the enrolled process, not a client poll; ``comms_poll_interval_seconds``
    #: still governs how often a CLIENT should call.
    comms_wait_interval_ms: int = Field(
        default=1000, ge=100, le=15000, description="Sleep between bounded inbox wait attempts, in milliseconds."
    )

    #: Durable-delivery horizon (PRD-CORE-274 FR13, Amendment 02): a row that is not
    #: ACKed expires at ``admitted_at`` plus this, with an ``expired`` milestone.
    comms_message_ttl_seconds: int = Field(
        default=86400, ge=300, le=604800, description="Seconds an unacknowledged comms message stays deliverable."
    )
    comms_candidate_ttl_seconds: int = Field(
        default=3600, ge=60, le=86400, description="Seconds an announced comms candidate stays admissible."
    )

    @model_validator(mode="after")
    def _refuse_undeliverable_comms_bounds(self) -> Self:
        """Refuse configurations that would admit messages nobody can fetch.

        Both checks REFUSE rather than clamp. Clamping would leave the operator
        with a running system whose stated limits are not its real ones, and the
        first symptom would be a message that was accepted and then could never
        be read back — exactly the silent loss this PRD forbids.
        """

        required = _JSON_ESCAPE_WORST_CASE * self.comms_body_max_bytes + _RESPONSE_ENVELOPE_FLOOR_BYTES
        if self.comms_response_max_bytes < required:
            raise ValueError(
                f"comms_response_max_bytes ({self.comms_response_max_bytes}) must be at least "
                f"{_JSON_ESCAPE_WORST_CASE} * comms_body_max_bytes + {_RESPONSE_ENVELOPE_FLOOR_BYTES} "
                f"= {required}; otherwise a maximally escaped body is admitted but never deliverable"
            )
        min_lease = _MIN_LEASE_POLL_PERIODS * self.comms_poll_interval_seconds
        if self.comms_lease_ttl_seconds < min_lease:
            raise ValueError(
                f"comms_lease_ttl_seconds ({self.comms_lease_ttl_seconds}) must be at least "
                f"{_MIN_LEASE_POLL_PERIODS} * comms_poll_interval_seconds = {min_lease}; "
                f"a shorter lease fences a peer that is polling correctly"
            )
        return self
