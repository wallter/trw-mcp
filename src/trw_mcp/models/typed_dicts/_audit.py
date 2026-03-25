"""Audit TypedDicts — all Audit* types from audit.py."""

from __future__ import annotations

from typing_extensions import TypedDict


class AuditTelemetryBloatDict(TypedDict):
    """Telemetry-bloat sub-dict inside ``AuditLearningsResult``."""

    count: int
    pct: float
    verdict: str


class AuditLearningsResult(TypedDict):
    """Return shape of ``_audit_learnings()``."""

    total: int
    by_status: dict[str, int]
    by_impact: dict[str, int]
    top_tags: list[tuple[str, int]]
    by_source: dict[str, int]
    telemetry_bloat: AuditTelemetryBloatDict


class AuditDuplicatePairDict(TypedDict):
    """One duplicate pair inside ``AuditDuplicatesResult``."""

    older_id: str
    newer_id: str
    similarity: float


class AuditDuplicatesResult(TypedDict):
    """Return shape of ``_audit_duplicates()``."""

    pairs: list[AuditDuplicatePairDict]
    count: int
    verdict: str


class AuditIndexConsistencyResult(TypedDict, total=False):
    """Return shape of ``_audit_index_consistency()``.

    ``analytics_total`` is ``None`` when analytics.yaml is absent (verdict
    becomes ``"SKIP"``).  ``match`` is absent on the SKIP path.
    """

    analytics_total: int | None
    actual_count: int
    match: bool
    verdict: str


class AuditRecallEffectivenessResult(TypedDict, total=False):
    """Return shape of ``_audit_recall_effectiveness()``.

    Short form (no recall log): only ``total_queries`` and ``verdict``
    (``"SKIP"``) are present.  Full form has all keys.
    """

    total_queries: int
    wildcard_queries: int
    named_queries: int
    zero_match: int
    miss_rate: float
    top_zero_match_queries: list[str]
    verdict: str


class AuditCeremonyComplianceResult(TypedDict):
    """Return shape of ``_audit_ceremony_compliance()``."""

    runs_scanned: int
    avg_ceremony_score: float
    build_pass_rate: float
    avg_learnings_per_run: float
    verdict: str


class AuditReflectionComponentsDict(TypedDict):
    """Component scores inside ``AuditReflectionQualityResult``."""

    reflection_frequency: float
    productivity: float
    diversity: float
    access_ratio: float
    q_activation_rate: float


class AuditReflectionDiagnosticsDict(TypedDict):
    """Diagnostics sub-dict inside ``AuditReflectionQualityResult``."""

    reflection_count: int
    avg_learnings_per_reflection: float
    total_entries: int
    active_entries: int
    accessed_entries: int
    q_activated_entries: int
    unique_tags: int
    source_types: list[str]


class AuditReflectionQualityResult(TypedDict):
    """Return shape of ``_audit_reflection_quality()`` / ``compute_reflection_quality()``."""

    score: float
    components: AuditReflectionComponentsDict
    diagnostics: AuditReflectionDiagnosticsDict


class AuditHookVersionsResult(TypedDict):
    """Return shape of ``_audit_hook_versions()``.

    When the hooks directory is absent, ``total`` is 0, ``up_to_date`` is 0,
    ``outdated`` is ``[]``, and ``verdict`` is ``"SKIP"``.
    """

    total: int
    up_to_date: int
    outdated: list[str]
    verdict: str


class AuditFixActionsDict(TypedDict, total=False):
    """Fix-actions sub-dict added to ``AuditReport`` when ``fix=True``."""

    telemetry_bloat_retired: int
    prune: dict[str, object]
    index_resynced: bool


class AuditReport(TypedDict, total=False):
    """Return shape of ``run_audit()``.

    All section keys are always present on the success path.  ``error`` and
    ``status="failed"`` are the only keys present on the early-exit (no .trw)
    path.  ``fix_actions`` is only present when ``fix=True``.
    """

    project: str
    target_dir: str
    generated_at: str
    learnings: AuditLearningsResult
    duplicates: AuditDuplicatesResult
    index_consistency: AuditIndexConsistencyResult
    recall_effectiveness: AuditRecallEffectivenessResult
    ceremony_compliance: AuditCeremonyComplianceResult
    reflection_quality: AuditReflectionQualityResult
    hook_versions: AuditHookVersionsResult
    fix_actions: AuditFixActionsDict
    status: str
    error: str
