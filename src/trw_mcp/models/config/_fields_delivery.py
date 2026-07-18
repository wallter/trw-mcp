"""Crash-safe delivery operation config fields — PRD-CORE-208."""

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
