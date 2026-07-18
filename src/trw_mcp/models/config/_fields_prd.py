"""PRD-authoring convention fields for TRWConfig.

Ships with a minimal generic set of PRD categories (CORE/QUAL/INFRA/LOCAL/
EXPLR/RESEARCH/FIX). Any project using trw-mcp may extend the accepted
category set by listing additional names in `.trw/config.yaml`:

    extra_prd_categories:
      - EVAL
      - HPO
      - INTENT
      - SCALE

The union of built-in + extra categories is what `trw_prd_create` and
`trw_prd_validate` accept.

See also: `trw_mcp.state.validation.prd_integrity.allowed_prd_categories()`.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class _PRDFields(BaseSettings):
    """Configuration fields governing PRD authoring + validation."""

    extra_prd_categories: list[str] = Field(
        default_factory=list,
        description=(
            "Project-specific PRD category names accepted in addition to the "
            "built-in generic set (CORE, QUAL, INFRA, LOCAL, EXPLR, RESEARCH, "
            "FIX). Case-insensitive; compared upper-case at validation time. "
            "Example (TRW monorepo): "
            "['EVAL', 'HPO', 'INTENT', 'SCALE', 'THRASH', 'SEC', 'DIST']."
        ),
    )

    # --- PRD path-index walk bounds (trw_prd_validate hot path) ---
    # The bare-filename resolver in ``_prd_integrity_paths.py`` builds a
    # basename index via a single ``os.walk`` of the repo. In a large monorepo
    # that walk is O(files) and dominated validate latency (~8s for one PRD).
    # These knobs (a) let a project prune project-specific bulk trees the shared
    # constant cannot know about, and (b) bound the walk so it can never scan an
    # unbounded number of files. When either cap trips the index is marked
    # PARTIAL and the resolver degrades to advisory-skip (never emits a false
    # "no match in repo" warning from a truncated index).
    path_index_exclude_dirs: list[str] = Field(
        default_factory=list,
        description=(
            "Extra directory names pruned from the PRD path-index walk, unioned "
            "with the built-in PATH_INDEX_EXCLUDE_DIRS. Use for project-specific "
            "bulk trees (e.g. eval data corpora) that are never a valid PRD path "
            "reference target."
        ),
    )
    path_index_max_files: int = Field(
        default=1_000_000,
        ge=1,
        description=(
            "RUNAWAY GUARD (not the latency control) on files scanned while "
            "building the bare-filename basename index. Pruning "
            "(PATH_INDEX_EXCLUDE_DIRS + path_index_exclude_dirs) plus "
            "path_index_max_seconds are the real latency controls; this cap only "
            "stops a pathological walk from scanning an unbounded tree. Tripping "
            "it DEGRADES the check: the index is marked PARTIAL and bare-filename "
            "grounding is skipped (and reported via integrity_warnings), so set it "
            "high enough that a healthy monorepo indexes COMPLETELY. A too-low cap "
            "silently truncates real repos and disables hallucinated-path detection."
        ),
    )
    path_index_max_seconds: float = Field(
        default=5.0,
        gt=0.0,
        description=(
            "RUNAWAY GUARD (wall-clock seconds) on the bare-filename basename "
            "index walk. Together with directory pruning this bounds validate "
            "latency. Tripping it DEGRADES the check: the index is marked PARTIAL "
            "and bare-filename grounding is skipped (and reported via "
            "integrity_warnings). Prefer pruning bulk trees via "
            "path_index_exclude_dirs over lowering this — a truncated index cannot "
            "detect hallucinated file references."
        ),
    )

    # --- Never-hang budget guard for the DYNAMIC validate portion (PRD-FIX-112) ---
    # Cooperative wall-clock budget for the DYNAMIC portion of
    # ``trw_prd_validate`` (grounded re-scoring + repo/wiring/duplicate integrity
    # checks). When exceeded mid-run the remaining dynamic check groups are
    # SKIPPED and the result is marked ``validation_partial=True`` (never a hang,
    # never a silent pass) so a future slowdown can never re-train agents to
    # bypass the gate. Distinct from the path-index runaway guards above: those
    # bound one sub-walk, this bounds the whole dynamic overlay. The historical
    # 20-min hang was already fixed (largest PRDs now refresh in 0.27-0.55s); the
    # default 60.0s is a generous anti-regression ceiling normal PRDs never
    # approach.
    prd_validate_budget_seconds: float = Field(
        default=60.0,
        gt=0.0,
        description=(
            "Wall-clock budget (seconds) for trw_prd_validate's dynamic checks; "
            "on breach the remaining check groups are skipped and the result is "
            "flagged validation_partial (PRD-FIX-112)."
        ),
    )

    # --- PRD-validation content-addressed cache bounds (PRD-QUAL-114-FR08) ---
    # The pure text/config validation result is cached under
    # ``.trw/cache/prd-validation/v2`` as independent atomic JSON shards. The
    # cache is DISPOSABLE derived state: corruption or contention degrades to a
    # miss and never changes validation truth. These knobs bound the shard store
    # so it cannot grow without limit (the legacy monolithic YAML reached 167
    # entries / 2.4 MB on 2026-07-09). Deterministic oldest-first maintenance
    # keeps the store within BOTH the entry and byte ceilings.
    prd_validation_cache_max_entries: int = Field(
        default=512,
        ge=1,
        description=(
            "Maximum number of cached pure-validation shards. When exceeded, "
            "deterministic (accessed_at, cache_key) oldest-first eviction runs "
            "under a maintenance lock, always preserving the just-written entry. "
            "Eviction affects only cache hit-rate, never validation correctness."
        ),
    )
    prd_validation_cache_max_total_bytes: int = Field(
        default=64 * 1024 * 1024,
        ge=1024,
        description=(
            "Maximum aggregate on-disk bytes across all cached pure-validation "
            "shards. When exceeded, oldest-first eviction runs alongside the "
            "entry-count ceiling. Disposable acceleration state only."
        ),
    )
    prd_validation_cache_max_entry_bytes: int = Field(
        default=4 * 1024 * 1024,
        ge=256,
        description=(
            "Maximum size of a single cached shard. A stored payload larger than "
            "this is rejected at write time and an on-disk shard larger than this "
            "is treated as a corrupt cache MISS at read time (bounded degradation)."
        ),
    )
    prd_validation_cache_maintenance_interval: int = Field(
        default=32,
        ge=1,
        description=(
            "Number of successful shard writes between bounded maintenance "
            "sweeps. A persistent per-cache write counter (updated under the "
            "maintenance advisory lock) triggers one deterministic dual-cap "
            "eviction sweep every N writes. Lower values keep the cache tighter "
            "at the cost of more frequent directory enumeration; correctness is "
            "independent of this cadence."
        ),
    )
