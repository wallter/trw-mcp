"""Memory-truthfulness configuration fields — PRD-CORE-231 (Track R).

Tunables for the verification sweep (FR02) and the T2 edit-time hint delivery
path (FR01). Every value here is a typed, bounded field precisely so the
consuming code carries no magic numbers (NFR03).
"""

from __future__ import annotations

from pydantic import Field


class _VerificationFields:
    """Verification/hint-delivery domain mixin — mixed into _TRWConfigFields."""

    # -- FR02: maintain-verify batch sweep ---------------------------------
    #: Max entries-with-assertions pulled in the sweep's single bulk fetch.
    #: 1000 is the entry count NFR01 budgets at <30s; raise only with a fresh
    #: measurement, since the bound exists to keep a scheduled run bounded.
    maintain_verify_batch_limit: int = Field(
        default=1000,
        ge=1,
        le=100_000,
        description="Max entries the maintain-verify assertion sweep processes per run.",
    )

    # -- FR01: T2 hint sidecar generation + delivery measurement -----------
    #: Master switch for the post-commit sidecar refresh. Turning this off is
    #: the FR01 rollback path (§9): trw_before_edit_hint degrades to its
    #: existing T1/T0 behavior with no code change.
    hint_sidecar_refresh_enabled: bool = True

    #: Files per commit the post-commit refresh will regenerate a sidecar for.
    #: Bounded so a large commit cannot stall `git commit` indefinitely (NFR01).
    hint_sidecar_refresh_file_cap: int = Field(
        default=20,
        ge=1,
        le=500,
        description="Max changed files the post-commit sidecar refresh processes per commit.",
    )

    #: Delivery-rate gate for the T2 tier (FR01 / NFR02). 0.90 is the
    #: synthesis-specified >=90% of eligible edits.
    hint_delivery_rate_min: float = Field(
        default=0.90,
        ge=0.0,
        le=1.0,
        description="Minimum share of eligible edits that must receive a T2 hint.",
    )

    #: Trailing window over which the delivery rate is measured.
    hint_delivery_measurement_window_days: int = Field(
        default=14,
        ge=1,
        description="Trailing-window length (days) for the hint-delivery rate measurement.",
    )
