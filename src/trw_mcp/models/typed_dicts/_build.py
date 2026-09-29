"""Build check result TypedDicts (tests, static checks, dep audit, fuzz)."""

from __future__ import annotations

from typing_extensions import TypedDict


class TestResultDict(TypedDict, total=False):
    """Result from a project-native test/check runner."""

    tests_passed: bool
    coverage_pct: float
    test_count: int
    failure_count: int
    failures: list[str]
    timed_out: bool


class StaticCheckResultDict(TypedDict, total=False):
    """Result from a project-native static/type/lint/schema check."""

    static_checks_clean: bool
    mypy_clean: bool
    mypy_error_count: int
    failures: list[str]
    timed_out: bool
