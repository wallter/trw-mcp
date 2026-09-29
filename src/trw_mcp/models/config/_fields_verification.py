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
    #: Post-commit's sidecar work: the detached rebuild request (8.2 S2b) and
    #: the risk-report refresh. Off, post-commit does neither; the pre-edit
    #: hint's own rebuild request is gated by hint_sidecar_auto_refresh_enabled.
    hint_sidecar_refresh_enabled: bool = True

    # -- T2 ancestor sidecar (8.2 slice S1): the pre-edit hint read path -----
    #: Accept the nearest cached batch sidecar whose sha is a proven ancestor
    #: of HEAD when no exact-HEAD sidecar exists, labelled "as of <sha>" and
    #: filtered per file. Off restores the exact-HEAD-only lookup exactly.
    hint_sidecar_ancestor_enabled: bool = True

    #: The farthest an ancestor sidecar may sit behind HEAD, in commits. 500
    #: matches the co-change history window the sidecar was built from; past it
    #: the sidecar's window no longer overlaps HEAD's.
    hint_sidecar_max_commits_behind: int = Field(
        default=500,
        ge=1,
        le=10_000,
        description=(
            "Commits an ancestor sidecar may trail HEAD by and still serve the pre-edit hint "
            "(1-10000; lower refuses older sidecars as sidecar_too_far_behind)."
        ),
    )

    # -- T2 sidecar rebuild request (8.2 slice S2b) ------------------------
    #: Let the pre-edit hint and post-commit request a detached, niced
    #: `trw-distill self-improve refresh-sidecars` build when no usable
    #: sidecar exists or the nearest trails HEAD by the threshold below.
    #: Off, nothing is ever spawned.
    hint_sidecar_auto_refresh_enabled: bool = True

    #: An ancestor sidecar this many commits behind HEAD (or more) triggers a
    #: rebuild request while it still serves the hint. A value above
    #: hint_sidecar_max_commits_behind acts as that bound (no ancestor past it serves).
    hint_sidecar_rebuild_after_commits: int = Field(
        default=150,
        ge=1,
        le=10_000,
        description=(
            "Commits behind HEAD at which a served ancestor sidecar triggers a rebuild request "
            "(1-10000, capped at hint_sidecar_max_commits_behind; lower rebuilds more often)."
        ),
    )

    #: The fewest minutes between two rebuild requests from one cache dir.
    #: A whole-repo build takes minutes, so this bounds how often one starts.
    hint_sidecar_rebuild_min_interval_minutes: int = Field(
        default=15,
        ge=1,
        le=1440,
        description=(
            "Minimum minutes between two detached sidecar rebuild requests (1-1440; "
            "lower starts builds more often, higher leaves the sidecar older)."
        ),
    )

    # -- ANCHOR-HUB-DOWNRANK: the pre-edit hint on hub files ---------------
    #: On a hub file, show text-matched lessons before lessons whose only link
    #: to the file is an anchor. Off restores the anchored-first order exactly.
    hint_hub_downrank: bool = True

    #: A file is a hub when more than this many active lessons are anchored to
    #: it. The hint fetches up to this + 1 anchored rows per edit to decide, so
    #: it also bounds that page. 67 = the pooled 95th percentile of anchored
    #: lessons per file on the httpx + click benchmark stores (pre-registered).
    hint_hub_threshold: int = Field(
        default=67,
        ge=1,
        le=9_999,
        description=(
            "Anchored lessons above which a file is a hub whose anchor-only lessons the pre-edit hint "
            "shows after text-matched ones (1-9999; lower flags more files, each edit fetches this + 1 rows)."
        ),
    )

    #: PRD-DIST-2482 FR04: spawn a detached `trw-distill run --incremental
    #: --live-ingest` after each commit. Opt-in: the run may do an LLM pass.
    post_commit_distill_incremental: bool = False

    # -- HINT-DELIVERY-CANARY: the ``hint_delivery`` doctor row -------------
    #: Newest N ``.trw/context/cc03-hints/*.json`` records the doctor row reads
    #: by mtime. Bounds the read so a large project cannot make the row slow;
    #: 200 is large enough to see a fallback-dominated run without reading a
    #: whole project history.
    hint_delivery_canary_window: int = Field(
        default=200,
        ge=20,
        le=5000,
        description="Newest N pre-edit hint records the hint_delivery doctor row inspects (20-5000).",
    )

    #: Share of the window whose distill_status is exception_fallback or
    #: timeout_fallback at or above which the row WARNs. 0.5 means "half or
    #: more of recent edits fell back" is worth an operator's attention.
    hint_delivery_fallback_warn_share: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Fallback-status share of the hint_delivery window at/above which the row WARNs (0-1).",
    )

    #: Age of ``.trw/runtime/post-commit-receipt.json`` beyond which the
    #: hint_delivery row WARNs, when trw-distill is installed (a stale receipt
    #: on a distill-enabled checkout means the sidecar refresh has stopped
    #: producing sidecars). No effect when trw-distill is absent.
    hint_delivery_receipt_stale_days: int = Field(
        default=7,
        ge=1,
        le=365,
        description="Post-commit sidecar receipt age (days) after which hint_delivery WARNs when trw-distill is installed.",
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

    # recall_verification_budget_ms was removed in trw-mcp 8.0.0 (PRD-CORE-313-FR06): a
    # compatibility-only input since PRD-CORE-268 retired inline verification. The key
    # is listed in trw_mcp/data/config-retired-keys.json.

    # -- HINT-RECALL-BUDGET: the pre-edit hint's T1 memory recall ----------
    #: Wall-clock budget for the pre-edit hint's whole T1 learnings recall
    #: (trw_mcp.tools._before_edit_hint_core._collect_learnings), single-paged
    #: (no take_hits growth loop). Past this, the hint returns no learnings
    #: (``learnings_status: "recall_timeout"``) and still returns the T2
    #: sidecar hint; the abandoned recall is never waited on. Other recall
    #: callers (the trw_recall tool, trw_session_start) are unaffected.
    hint_recall_deadline_ms: int = Field(
        default=600,
        ge=50,
        le=10_000,
        description=(
            "Wall-clock budget (ms) for the pre-edit hint's T1 learnings recall (50-10000); "
            "past it the hint returns no learnings as recall_timeout rather than block the T2 sidecar hint."
        ),
    )
