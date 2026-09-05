"""Delivery-path config fields — PRD-CORE-208 crash safety, PRD-CORE-255 sign-offs."""

from __future__ import annotations

from typing import Literal

from pydantic import Field


class _DeliveryFields:
    """Delivery-operation domain mixin — mixed into _TRWConfigFields via MI.

    Wave-tunable operational bounds only — the fixed v1 lifecycle caps
    (30/90/180-day retention, 64 MiB / 20k-row hard caps, 128 KiB record cap)
    are documented constants in ``tools/_delivery_request.py::DeliveryLimits``,
    not tunables.
    """

    delivery_operations_mode: Literal["off", "observe", "enforce"] = "enforce"
    delivery_stale_lease_minutes: int = Field(
        default=15, ge=1, le=1440, description="Minutes a pending lease must be stale before FR04 takeover."
    )
    delivery_queue_depth_max: int = Field(
        default=128, ge=1, le=1024, description="Bounded deferred FIFO queue depth (FR06)."
    )
    delivery_busy_timeout_ms: int = Field(
        default=5000, ge=100, le=60000, description="SQLite busy timeout for the delivery operation store."
    )
    #: PRD-CORE-255-FR04 (2026-09-04 Amendment 2): the MAXIMUM validity window an
    #: operator may grant a review sign-off in
    #: ``<trw_dir>/approvals/review-signoffs.jsonl``, which is what the
    #: safety-critical adversarial gate resolves an operator receipt id against.
    #: 24h matches review_verdict_ttl_hours so an approval cannot outlive the
    #: verdict it authorizes. Applied twice — the default TTL at mint time AND a
    #: cap at verification — so a hand-written longer window is refused
    #: (operator_approval_ttl_exceeded) rather than honored. Bounded ge=1/le=720:
    #: 0 would expire every approval instantly and wedge the operator path, and
    #: 30 days is the longest sign-off that can still describe today's code.
    review_signoff_ttl_hours: int = Field(
        default=24, ge=1, le=720, description="Max validity window of an operator review sign-off, in hours."
    )
