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

    # -- PRD-CORE-244-FR03: a positive verification verdict ----------------
    #: Lowest recomputed anchor score a "verified" verdict tolerates. At the
    #: default 1.0 a single moved anchor is enough to withhold the positive
    #: verdict, which is the conservative reading: "verified" is a claim the
    #: pass makes on the operator's behalf, so it must not survive a partially
    #: drifted anchor set. Lower it only with evidence that partial drift is
    #: acceptable in a given repo.
    anchor_validity_verified_floor: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Minimum recomputed anchor_validity for a 'verified' verification verdict.",
    )

    #: How long a persisted verification verdict stays reusable. Within this
    #: window the recall pass reuses ``verification_checked_at``'s verdict and
    #: performs NO filesystem verification for that entry. 3600s bounds the
    #: staleness of a reused verdict to an hour while removing the repeated
    #: per-recall filesystem scan that dominated the pass. 0 disables reuse.
    verification_cache_ttl_seconds: int = Field(
        default=3600,
        ge=0,
        le=604_800,
        description="Seconds a persisted verification verdict is reused before the pass re-checks an entry.",
    )
