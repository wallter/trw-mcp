"""Mutation testing result TypedDicts (mutations.py boundary types)."""

from __future__ import annotations

from typing import TypedDict


class SurvivingMutantDict(TypedDict):
    """One surviving mutant from mutmut results."""

    file: str
    line: int
    description: str


class ParseMutmutResultDict(TypedDict, total=False):
    """Return shape of ``_parse_mutmut_results()``.

    Error path: ``parse_error`` is set and counts are all zero.
    Success path: ``mutation_score`` is a float (or None when no mutants ran).
    """

    killed: int
    survived: int
    timeout: int
    suspicious: int
    total_mutants: int
    mutation_score: float | None
    surviving_mutants: list[SurvivingMutantDict]
    parse_error: str


class MutationSkippedResult(TypedDict):
    """Return shape of ``run_mutation_check()`` when skipped."""

    mutation_skipped: bool
    mutation_skip_reason: str


class MutationCheckResult(TypedDict, total=False):
    """Return shape of ``run_mutation_check()`` when a full run completes.

    The parsed-result keys (killed, survived, timeout, suspicious,
    total_mutants, surviving_mutants) are merged from ``ParseMutmutResultDict``
    at runtime via ``**parsed``.
    """

    mutation_passed: bool
    mutation_score: float | None
    mutation_tier: str
    mutation_threshold: float
    changed_files: list[str]
    changed_file_count: int
    # merged from ParseMutmutResultDict (excluding mutation_score)
    killed: int
    survived: int
    timeout: int
    suspicious: int
    total_mutants: int
    surviving_mutants: list[SurvivingMutantDict]
    parse_error: str
