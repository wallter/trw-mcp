"""Review helper TypedDicts (_review_helpers.py boundary types)."""

from __future__ import annotations

from typing_extensions import TypedDict


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
    """Return shape of ``handle_reconcile_mode()``.

    Honesty qualifiers (do NOT read ``verdict='clean'`` as "FRs verified covered"):

    - ``coverage_method``: how coverage was assessed. Reconcile does
      *identifier-presence-in-diff* substring matching, NOT behavioral
      verification, so this is the literal string
      ``'identifier_presence_in_diff'``.
    - ``fr_not_checkable``: FRs with no extractable identifier. These are
      surfaced here instead of being silently counted as covered.
    - ``not_checkable_count``: count of ``fr_not_checkable`` entries.
    - ``no_governing_prd`` / ``reason``: set when no governing PRD was found,
      so a ``'clean'`` verdict is not misread as "FRs verified covered".
    """

    review_id: str
    verdict: str
    mismatches: list[dict[str, str]]
    message: str
    prd_count: int
    total_frs: int
    mismatch_count: int
    reconciliation_yaml: str
    coverage_method: str
    fr_not_checkable: list[dict[str, str]]
    not_checkable_count: int
    no_governing_prd: bool
    reason: str


class MultiReviewerAnalysisResult(TypedDict, total=False):
    """Return shape of ``_run_multi_reviewer_analysis()``.

    ``auto_analysis_limited`` (with ``limited_reason``) honestly labels the
    pattern-scan-only path: when set ``True`` the ``findings`` come solely from
    a TODO/FIXME/HACK/XXX marker scan of the diff, NOT from substantive
    multi-reviewer / cross-model code-quality analysis. Downstream consumers
    (deliver gate, eval scoring) must treat a limited result as a weak signal,
    never as evidence that a real review happened.
    """

    reviewer_roles_run: list[str]
    reviewer_errors: list[str]
    findings: list[dict[str, object]]
    auto_analysis_limited: bool
    limited_reason: str


class AutoReviewResult(TypedDict, total=False):
    """Return shape of ``handle_auto_mode()``.

    ``auto_analysis_limited``/``limited_reason`` propagate the honest-labeling
    flag from ``_run_multi_reviewer_analysis()`` so the auto-review artifact
    cannot pose as a substantive review when only the pattern-scan ran.
    """

    review_id: str
    verdict: str
    mode: str
    reviewer_roles_run: list[str]
    reviewer_errors: list[str]
    surfaced_findings_count: int
    total_findings_count: int
    confidence_threshold: int
    critical_count: int
    run_path: str | None
    review_yaml: str
    auto_analysis_limited: bool
    limited_reason: str
