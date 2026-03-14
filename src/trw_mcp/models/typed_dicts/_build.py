"""Build check result TypedDicts (pytest, mypy, dep audit, fuzz)."""

from __future__ import annotations

from typing import TypedDict


class PytestResultDict(TypedDict, total=False):
    """Result from ``_run_pytest``."""

    tests_passed: bool
    coverage_pct: float
    test_count: int
    failure_count: int
    failures: list[str]


class MypyResultDict(TypedDict, total=False):
    """Result from ``_run_mypy``."""

    mypy_clean: bool
    mypy_error_count: int
    failures: list[str]


class PipAuditResult(TypedDict, total=False):
    """Return shape of ``_run_pip_audit()``.

    Skipped path: ``pip_audit_skipped=True`` and ``pip_audit_skip_reason``.
    Success path: ``pip_audit_passed``, counts, and ``pip_audit_vulnerabilities``.
    """

    pip_audit_passed: bool
    pip_audit_vulnerability_count: int
    pip_audit_blocking_count: int
    pip_audit_vulnerabilities: list[dict[str, object]]
    pip_audit_skipped: bool
    pip_audit_skip_reason: str


class NpmAuditResult(TypedDict, total=False):
    """Return shape of ``_run_npm_audit()``.

    Skipped path: ``npm_audit_skipped=True`` and ``npm_audit_skip_reason``.
    Success path: ``npm_audit_passed``, count, and ``npm_audit_vulnerabilities``.
    """

    npm_audit_passed: bool
    npm_audit_high_plus_count: int
    npm_audit_vulnerabilities: list[dict[str, object]]
    npm_audit_skipped: bool
    npm_audit_skip_reason: str


class DepAuditResult(TypedDict, total=False):
    """Return shape of ``_run_dep_audit()``.

    Always present: ``dep_audit_passed``, ``timestamp``.
    pip-audit keys merged from ``PipAuditResult``.
    npm-audit keys merged from ``NpmAuditResult``.
    Unlisted-import keys present only when unlisted imports are detected.
    """

    dep_audit_passed: bool
    timestamp: str
    # pip-audit sub-result keys (merged)
    pip_audit_passed: bool
    pip_audit_vulnerability_count: int
    pip_audit_blocking_count: int
    pip_audit_vulnerabilities: list[dict[str, object]]
    pip_audit_skipped: bool
    pip_audit_skip_reason: str
    # npm-audit sub-result keys (merged)
    npm_audit_passed: bool
    npm_audit_high_plus_count: int
    npm_audit_vulnerabilities: list[dict[str, object]]
    npm_audit_skipped: bool
    npm_audit_skip_reason: str
    # unlisted imports (present only when imports detected)
    unlisted_imports: list[str]
    unlisted_import_count: int


class ApiFuzzResult(TypedDict, total=False):
    """Return shape of ``_run_api_fuzz()``.

    Skipped path: ``api_fuzz_skipped=True`` and ``api_fuzz_skip_reason``.
    Success path: ``api_fuzz_passed``, ``api_fuzz_base_url``, and optional
    ``api_fuzz_failures`` / ``api_fuzz_failure_count``.
    """

    api_fuzz_passed: bool
    api_fuzz_base_url: str
    api_fuzz_failures: list[str]
    api_fuzz_failure_count: int
    api_fuzz_skipped: bool
    api_fuzz_skip_reason: str
