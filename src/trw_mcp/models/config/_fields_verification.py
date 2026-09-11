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
    #: CORE268: maximum decoded entries per page, including anchor-only entries.
    #: The sweep traverses multiple pages; this is not a total-work, runtime,
    #: byte-size or staleness bound, and no scheduler is installed.
    maintain_verify_batch_limit: int = Field(
        default=1000,
        ge=1,
        le=100_000,
        description="Max entries per maintain-verify page; the explicit sweep traverses all eligible pages.",
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

    #: CORE268: age qualification for stored evidence, not current-tree truth.
    #: Expiry never launches a scan; explicit maintain-verify refreshes evidence.
    #: Zero means no fresh classification, not unconditional inline verification.
    verification_cache_ttl_seconds: int = Field(
        default=3600,
        ge=0,
        le=604_800,
        description="Freshness window for last-known evidence; never proves current-tree validity or triggers verification.",
    )

    #: Compatibility-only input since CORE268 removed inline verification.
    #: It does not bound recall or maintenance runtime. Retain parsing for old
    #: configuration files until a deliberate configuration migration removes it.
    recall_verification_budget_ms: int = Field(
        default=1000,
        ge=0,
        le=600_000,
        description="Deprecated compatibility input; inline recall verification is retired and this value has no effect.",
    )

    # -- PRD-CORE-267-FR03: the shared-anchor-set migration ------------------
    #: Minimum number of entries sharing one IDENTICAL anchor set before that
    #: set is treated as derivation noise rather than topical convergence. On
    #: the development store (2,006 anchored rows, 370 distinct sets) 344 sets
    #: hold 7 members or fewer and the distribution is empty at 9, so 10 sits
    #: below every fabricated cluster and above every plausible case of several
    #: learnings genuinely concerning the same symbols.
    anchor_shared_set_migration_threshold: int = Field(
        default=10,
        ge=2,
        le=10_000,
        description="Entries sharing one identical anchor set before the clear-shared-anchors migration selects it.",
    )
