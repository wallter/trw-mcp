"""Failure attribution for ``trw_build_check`` (PRD-IMPROVE-MCP-02 FR1).

Belongs to the ``tools/build`` facade. Re-exported via
``tools/build/__init__.py`` for back-compat.

Problem it solves: when a reported test/check FAILS, an agent must answer
"did MY uncommitted change cause this, or was it already broken?" — today
that means git archaeology. This module gives a fast, fail-open triage
signal so the answer is one glance at the build_check result.

Design (deliberately a HEURISTIC, not proof — see honest limits below):

1. Collect the current working-tree change set: ``git diff --name-only HEAD``
   (unstaged-vs-HEAD) UNION ``git diff --name-only --cached`` (staged).
2. For each failing test string, best-effort extract the test file path,
   then derive candidate production-module name stems from it.
3. A failure is ``likely_introduced`` when its own test file OR an
   obviously-related source file appears in the change set; otherwise
   ``likely_pre_existing``. If git is unavailable or the failure cannot be
   parsed, it degrades to ``unknown``.

HONEST LIMITS (also documented on the TypedDicts):
- The test->source mapping is name-stem based. It will MISS a failure whose
  root cause lives in an untouched transitive dependency of a touched file,
  and can FALSE-POSITIVE if an unrelated file shares a name stem.
- It does not run a baseline (that is out of scope and conflicts with the
  no-stash rule). It is a triage hint, never a verdict.

Fail-open contract: every public entry point catches broadly and degrades
to ``unknown`` rather than raising — attribution must NEVER break
``trw_build_check`` itself.
"""

from __future__ import annotations

import re
import subprocess

import structlog

from trw_mcp.models.typed_dicts._tools import (
    FailureAttributionDict,
    FailureAttributionItemDict,
)

logger = structlog.get_logger(__name__)

# A pytest nodeid / failure line usually starts with a path ending in ``.py``
# optionally followed by ``::test_name``. We also tolerate bare paths and
# common non-python suffixes so the heuristic is language-tolerant.
_PATH_RE = re.compile(r"(?P<path>[\w./\-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|rb))")

# Stems that carry no discriminating signal — never use them to match.
_NOISE_STEMS = frozenset({"test", "tests", "spec", "src", "init"})


def changed_files() -> set[str] | None:
    """Return the current working-tree change set, or ``None`` on git error.

    The set contains BOTH full relative paths (as git prints them) and the
    bare basenames, so a failure referencing either form matches. ``None``
    (not empty set) signals "git unavailable" so callers degrade to
    ``unknown`` rather than mis-tagging everything as pre-existing.
    """
    paths: set[str] = set()
    try:
        for args in (
            ["git", "diff", "--name-only", "HEAD"],
            ["git", "diff", "--name-only", "--cached"],
        ):
            result = subprocess.run(  # noqa: S603 — static literal git args, no user input
                args,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                # Not a git repo / detached weirdness — treat as unavailable.
                return None
            for line in result.stdout.splitlines():
                line = line.strip()
                if line:
                    paths.add(line)
                    paths.add(line.rsplit("/", 1)[-1])
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    return paths


def _extract_test_file(failure: str) -> str | None:
    """Best-effort: pull the source/test file path out of a failure string."""
    match = _PATH_RE.search(failure)
    if match is None:
        return None
    return match.group("path")


def _candidate_stems(test_file: str) -> set[str]:
    """Derive production-name stems a test file plausibly exercises.

    ``tests/test_foo_bar.py`` -> {``foo_bar``, ``foo``, ``bar``}.
    A non-test file contributes its own bare stem. Noise stems are dropped.
    """
    basename = test_file.rsplit("/", 1)[-1]
    stem = basename.rsplit(".", 1)[0]
    # Strip a leading/trailing test marker: test_foo / foo_test / foo_spec.
    core = re.sub(r"^test_|_test$|^spec_|_spec$", "", stem)
    parts = {core, *core.split("_")}
    return {p for p in parts if p and p not in _NOISE_STEMS}


def _matches_change_set(test_file: str | None, changed: set[str]) -> bool:
    """True if the test file or an obviously-related source file changed."""
    if test_file is None:
        return False
    basename = test_file.rsplit("/", 1)[-1]
    # Direct hit: the failing test's own file (or full path) is in the diff.
    if test_file in changed or basename in changed:
        return True
    # Related-source hit: a changed file whose stem matches a candidate stem.
    stems = _candidate_stems(test_file)
    if not stems:
        return False
    for changed_path in changed:
        if "." not in changed_path and "/" in changed_path:
            continue  # skip directory-only entries
        changed_stem = changed_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        changed_stem = re.sub(r"^test_|_test$|^spec_|_spec$", "", changed_stem)
        if changed_stem and changed_stem in stems:
            return True
    return False


def _attribute_one(failure: str, changed: set[str]) -> FailureAttributionItemDict:
    """Classify a single failure string against the change set."""
    test_file = _extract_test_file(failure)
    if test_file is None:
        return FailureAttributionItemDict(
            failure=failure,
            test_file=None,
            classification="unknown",
            reason="could not parse a file path from the failure description",
        )
    if _matches_change_set(test_file, changed):
        return FailureAttributionItemDict(
            failure=failure,
            test_file=test_file,
            classification="likely_introduced",
            reason="current working changes touch this test or a related source file",
        )
    return FailureAttributionItemDict(
        failure=failure,
        test_file=test_file,
        classification="likely_pre_existing",
        reason="no current change touches this test or its target",
    )


def attribute_failures(failures: list[str]) -> FailureAttributionDict | None:
    """Triage each failure as likely-introduced / pre-existing / unknown.

    Returns ``None`` when there are no failures (nothing to attribute).
    Fail-open: any error degrades EVERY failure to ``unknown`` and the
    block is still returned so the caller never has to special-case a
    crash.
    """
    if not failures:
        return None

    changed = changed_files()
    try:
        if changed is None:
            per_failure: list[FailureAttributionItemDict] = [
                FailureAttributionItemDict(
                    failure=f,
                    test_file=_extract_test_file(f),
                    classification="unknown",
                    reason="git unavailable — cannot compare against working-tree changes",
                )
                for f in failures
            ]
            changed_count = 0
        else:
            per_failure = [_attribute_one(f, changed) for f in failures]
            changed_count = len({p for p in changed if "." in p})
    except Exception as exc:  # justified: attribution must never break build_check
        logger.warning(
            "failure_attribution_degraded",
            outcome="error",
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
        )
        per_failure = [
            FailureAttributionItemDict(
                failure=f,
                test_file=None,
                classification="unknown",
                reason="attribution error — degraded to unknown",
            )
            for f in failures
        ]
        changed_count = 0

    introduced = sum(1 for p in per_failure if p["classification"] == "likely_introduced")
    pre_existing = sum(1 for p in per_failure if p["classification"] == "likely_pre_existing")
    unknown = sum(1 for p in per_failure if p["classification"] == "unknown")

    return FailureAttributionDict(
        likely_introduced=introduced,
        likely_pre_existing=pre_existing,
        unknown=unknown,
        changed_files_count=changed_count,
        per_failure=per_failure,
        summary=_render_summary(len(failures), introduced, pre_existing, unknown),
    )


def _render_summary(total: int, introduced: int, pre_existing: int, unknown: int) -> str:
    """Human/agent-readable one-liner for the build_check summary line."""
    parts: list[str] = []
    if introduced:
        parts.append(f"{introduced} likely yours")
    if pre_existing:
        parts.append(f"{pre_existing} pre-existing on this tree")
    if unknown:
        parts.append(f"{unknown} unknown")
    detail = ", ".join(parts) if parts else "no attribution"
    plural = "failure" if total == 1 else "failures"
    return f"{total} {plural}: {detail} (heuristic triage, not proof)"
