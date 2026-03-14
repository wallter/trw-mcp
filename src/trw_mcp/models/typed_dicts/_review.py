"""Review helper TypedDicts (_review_helpers.py boundary types)."""

from __future__ import annotations

from typing import TypedDict
from typing_extensions import NotRequired


class ReviewResultBase(TypedDict):
    """Shared fields for manual and cross-model review results."""

    review_id: str
    verdict: str
    total_findings: int


class ReviewFindingDict(TypedDict, total=False):
    """One finding from ``trw_review``."""

    reviewer_role: str
    confidence: int
    category: str
    severity: str
    description: str
    line: int


class ReviewModeResult(TypedDict, total=False):
    """Return shape of ``handle_manual_mode()`` and ``handle_cross_model_mode()``.

    All keys are present in practice; ``total=False`` allows incremental
    construction in each handler.

    Deprecated: prefer the mode-specific subtypes ``ManualReviewResult`` and
    ``CrossModelReviewResult`` for new code.
    """

    review_id: str
    verdict: str
    total_findings: int
    critical_count: int
    warning_count: int
    info_count: int
    run_path: str | None
    review_yaml: str
    # cross_model-specific keys
    mode: str
    cross_model_skipped: bool
    cross_model_provider: str


class ManualReviewResult(ReviewResultBase, total=False):
    """Return shape of ``handle_manual_mode()``.

    ``run_path`` and ``review_yaml`` are added after initial construction
    so they are declared ``NotRequired``.
    """

    critical_count: int
    warning_count: int
    info_count: int
    run_path: str | None
    review_yaml: str


class CrossModelReviewResult(ReviewResultBase, total=False):
    """Return shape of ``handle_cross_model_mode()``."""

    mode: str
    cross_model_skipped: bool
    cross_model_provider: str
    run_path: str | None
    review_yaml: str


class ReconcileReviewResult(TypedDict, total=False):
    """Return shape of ``handle_reconcile_mode()``."""

    review_id: str
    verdict: str
    mismatches: list[dict[str, str]]
    message: str
    prd_count: int
    total_frs: int
    mismatch_count: int
    reconciliation_yaml: str


class MultiReviewerAnalysisResult(TypedDict):
    """Return shape of ``_run_multi_reviewer_analysis()``."""

    reviewer_roles_run: list[str]
    reviewer_errors: list[str]
    findings: list[dict[str, object]]


class AutoReviewResult(TypedDict, total=False):
    """Return shape of ``handle_auto_mode()``."""

    review_id: str
    verdict: str
    mode: str
    reviewer_roles_run: list[str]
    reviewer_errors: list[str]
    surfaced_findings_count: int
    total_findings_count: int
    confidence_threshold: int
    run_path: str | None
    review_yaml: str
