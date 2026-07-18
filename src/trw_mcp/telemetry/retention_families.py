"""Typed per-family retention policy table (PRD-CORE-181-FR01 breadth).

Sibling of ``retention_registry.py`` — keeps the registry module lean. The
registry is the deletion *authority*; this module supplies the *policy* that
the honest bridge
(``scripts/_runtime_retention.register_family_artifact_classes``) uses to
register whole artifact families as retained classes. The goal: every
candidate under a known family classifies THROUGH the registry with a typed
reason instead of ``unregistered_artifact`` — extending FR01's fail-closed
gate from runtime sidecars to the eval-results, distill, and knowledge-store
families the inventory (``.trw/retention/inventory.json`` ``by_producer``
buckets, keyed on the first path segment under ``.trw``) shows.

Policy judgment is documented per entry in ``rationale``. Families whose
records are the source of truth (eval empirical evidence, knowledge stores)
register AUTHORITATIVE; the distill pipeline family registers DERIVED but
stays PERMANENT because no sub-family is proven regenerable-and-bounded.
Every family here is PERMANENT, so ``retention_days`` stays 0 (no magic
number is introduced — a bounded window would need its own justification).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.telemetry.retention_registry import (
    AuthorityClass,
    RetentionClass,
    SensitivityClass,
)


class FamilyRetentionPolicy(BaseModel):
    """Retention contract for one whole artifact family under ``.trw``.

    A family is a producer bucket (first path segment under ``.trw``). Every
    regular file beneath ``path_prefix`` inherits these classes when the
    honest bridge registers the family, so the deletion gate classifies them
    with a typed reason derived from THIS policy rather than treating them as
    unregistered.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    family: str = Field(min_length=1)  # producer bucket / registry ``producer``
    path_prefix: str = Field(min_length=1)  # repo-relative, e.g. ".trw/campaign"
    authority_class: AuthorityClass
    retention_class: RetentionClass
    sensitivity: SensitivityClass
    retention_days: int = Field(default=0, ge=0)  # only meaningful for BOUNDED_DAYS
    rationale: str = Field(min_length=1)  # documented policy judgment


FAMILY_OWNER = "retention-family-policy"


FAMILY_POLICIES: tuple[FamilyRetentionPolicy, ...] = (
    FamilyRetentionPolicy(
        family="eval-results-campaign",
        path_prefix=".trw/campaign",
        authority_class=AuthorityClass.AUTHORITATIVE,
        retention_class=RetentionClass.PERMANENT,
        sensitivity=SensitivityClass.INTERNAL,
        rationale=(
            "Eval campaign results are empirical evidence and the source of "
            "truth for every statistical claim, so the general deletion gate "
            "must retain them unconditionally (AUTHORITATIVE short-circuits "
            "before any age/digest check). Only FR08's receipt-gated "
            "compaction consumer — separate work, replay-verified with "
            "match_rate==1.0 and zero numeric delta — may ever compact them, "
            "never the suffix/age cleanup path. Marked INTERNAL because run "
            "manifests can carry project-internal task and scorer detail."
        ),
    ),
    FamilyRetentionPolicy(
        family="distill-artifacts",
        path_prefix=".trw/distill",
        authority_class=AuthorityClass.DERIVED,
        retention_class=RetentionClass.PERMANENT,
        sensitivity=SensitivityClass.INTERNAL,
        rationale=(
            "Cold-start distillation artifacts are DERIVED from git history "
            "but expensive to regenerate (full-history mining + LLM synthesis) "
            "and carry the provenance of shipped learnings; no sub-family is "
            "proven regenerable-and-bounded, so they retain PERMANENT rather "
            "than as a bounded class. Digest-verified duplicate judge-audit "
            "compaction stays with the dedicated compress-judge-audits path, "
            "which is out of scope for this general gate."
        ),
    ),
    FamilyRetentionPolicy(
        family="knowledge-store",
        path_prefix=".trw/knowledge",
        authority_class=AuthorityClass.AUTHORITATIVE,
        retention_class=RetentionClass.PERMANENT,
        sensitivity=SensitivityClass.INTERNAL,
        rationale=(
            "The knowledge-fabric store is an authoritative record of "
            "accumulated engineering knowledge and may contain "
            "project-internal detail, so it is never eligible for automated "
            "deletion and is classed INTERNAL."
        ),
    ),
    FamilyRetentionPolicy(
        family="memory-store",
        path_prefix=".trw/memory",
        authority_class=AuthorityClass.AUTHORITATIVE,
        retention_class=RetentionClass.PERMANENT,
        sensitivity=SensitivityClass.INTERNAL,
        rationale=(
            "The memory engine's SQLite store is the authoritative learning "
            "corpus behind trw_recall, so the whole subtree registers "
            "AUTHORITATIVE and the default gate always retains it. Transient "
            "WAL/SHM sidecars living here are re-registered on-demand as an "
            "OBSERVATIONAL/BOUNDED runtime-sidecar class by the "
            "--register-runtime-classes bridge when an operator explicitly "
            "opts them into cleanup; single-run last-writer semantics keep "
            "runtime reclaim available without weakening the default retain."
        ),
    ),
    FamilyRetentionPolicy(
        family="learnings-store",
        path_prefix=".trw/learnings",
        authority_class=AuthorityClass.AUTHORITATIVE,
        retention_class=RetentionClass.PERMANENT,
        sensitivity=SensitivityClass.INTERNAL,
        rationale=(
            "Persisted learning records are an authoritative knowledge asset "
            "shared across every session; retain unconditionally, classed "
            "INTERNAL for the same reason as the other knowledge stores."
        ),
    ),
)


def policy_for_path(rel_path: str) -> FamilyRetentionPolicy | None:
    """Return the family policy whose prefix owns ``rel_path`` (longest match).

    ``rel_path`` is repository-relative POSIX. Returns ``None`` when no known
    family claims the path (the caller counts these as
    ``unregistered_in_known_families`` only when the path itself lives under a
    family prefix — a path outside every prefix is simply not a family file).
    """
    best: FamilyRetentionPolicy | None = None
    for policy in FAMILY_POLICIES:
        prefix = policy.path_prefix.rstrip("/") + "/"
        owns = rel_path == policy.path_prefix or rel_path.startswith(prefix)
        if owns and (best is None or len(policy.path_prefix) > len(best.path_prefix)):
            best = policy
    return best
