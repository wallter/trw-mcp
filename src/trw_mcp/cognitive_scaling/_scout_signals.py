"""Scout signal computation — PRD-SCALE-001 FR01.

Belongs to the ``cognitive_scaling`` package facade; the ``scout.py`` module
re-exports the public ``compute_signals`` entry point.

The three signals are GROUNDED and LANGUAGE-AGNOSTIC (NFR04): they use only
``git``, ``grep``, and ``trw_recall`` — never a language parser (no ``ast``,
``tree-sitter``, ...). Each signal is independently fail-open: a failure marks
that signal ``*_available=False`` and never raises, so FR12 can degrade to
DIRECT when fewer than two signals are computable.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import structlog

from trw_mcp.models.cognitive_scaling import PrecedentGap, ScoutSignals

logger = structlog.get_logger(__name__)

#: Hard wall-clock cap per subprocess so a wedged git/grep never stalls the
#: session (NFR01 Scout p95 <= 2s; FR12 degrade on timeout).
_SUBPROCESS_TIMEOUT_S = 3.0

#: Symbol tokens shorter than this are too generic to grep meaningfully (they
#: explode fan-out with false hits), so they are skipped during blast-radius.
_MIN_SYMBOL_LEN = 3


def _extract_symbols(text: str, *, limit: int = 12) -> list[str]:
    """Pull candidate identifiers from PRD/task text (language-agnostic).

    Deterministic: identifiers are matched by a fixed regex, de-duped
    order-preserving, and capped. No language parser is used (NFR04).
    """
    seen: dict[str, None] = {}
    for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text):
        if len(tok) < _MIN_SYMBOL_LEN:
            continue
        if tok not in seen:
            seen[tok] = None
        if len(seen) >= limit:
            break
    return list(seen)


def _run(cmd: list[str], *, cwd: Path, ok_codes: tuple[int, ...] = (0,)) -> str | None:
    """Run a bounded read-only subprocess; return stdout or None on any error.

    ``ok_codes`` lists exit codes treated as success. A non-listed exit code
    (e.g. git's 128 outside a repo) returns None so the caller degrades the
    signal to unavailable rather than reading an empty success.
    """
    try:
        # S603 justified: cmd is a fixed argv list (git/grep + a regex-extracted
        # identifier guarded by ``--``); never a shell string and never raw user
        # input. Read-only, timeout-bounded, fail-open.
        proc = subprocess.run(  # noqa: S603
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("scout_subprocess_failed", cmd=cmd[0], exc_info=True)
        return None
    if proc.returncode not in ok_codes:
        return None
    return proc.stdout


def compute_blast_radius(symbols: list[str], *, project_root: Path, threshold: int) -> tuple[int, bool, bool]:
    """grep symbol fan-out over the worktree (FR01 blast_radius).

    Returns ``(fan_out_count, threshold_hit, available)``. ``available`` is
    False when grep cannot run at all OR there were no usable symbols.
    """
    if not symbols:
        return 0, False, False
    total = 0
    ran_any = False
    for sym in symbols:
        # ``grep -rn`` recursive match count. ``--`` guards symbols that look
        # like flags. Exit 1 = "no matches" (a valid success, not a failure).
        out = _run(["grep", "-rn", "--", sym, "."], cwd=project_root, ok_codes=(0, 1))
        if out is None:
            continue
        ran_any = True
        total += out.count("\n")
    if not ran_any:
        return 0, False, False
    return total, total >= threshold, True


def compute_churn(paths: list[str], *, project_root: Path, commit_threshold: int) -> tuple[int, int, bool, bool]:
    """git-log churn over the last 6 months (FR01 churn).

    Returns ``(commit_count, author_count, threshold_hit, available)``.
    Fail-open: a non-git repo or git failure marks the signal unavailable.
    """
    targets = paths or ["."]
    out = _run(
        ["git", "log", "--since=6 months ago", "--pretty=format:%H|%an", "--", *targets],
        cwd=project_root,
    )
    if out is None:
        return 0, 0, False, False
    lines = [ln for ln in out.splitlines() if ln.strip()]
    commits = len(lines)
    authors = len({ln.split("|", 1)[-1] for ln in lines})
    return commits, authors, commits >= commit_threshold, True


def compute_precedent_gap(query: str, *, trw_dir: Path) -> tuple[PrecedentGap, bool, bool]:
    """trw_recall precedent overlap (FR01 precedent_gap).

    Returns ``(gap, threshold_hit, available)``. A high gap (no precedent) is
    a "hit". Fail-open: a recall failure marks the signal unavailable.
    """
    try:
        from trw_mcp.state._memory_recall import recall_learnings

        hits = recall_learnings(
            trw_dir,
            query[:512],
            min_impact=0.6,
            max_results=10,
            compact=True,
        )
    except Exception:  # justified: fail-open per FR12 — recall must not crash Scout
        logger.debug("scout_precedent_recall_failed", exc_info=True)
        return "HIGH", False, False
    n = len(hits)
    if n >= 3:
        return "NONE", False, True
    if n >= 1:
        return "PARTIAL", False, True
    return "HIGH", True, True


def compute_signals(
    *,
    task_description: str,
    declared_paths: list[str],
    project_root: Path,
    trw_dir: Path,
    blast_radius_threshold: int,
    churn_commit_threshold: int,
) -> ScoutSignals:
    """Compute all three grounded signals (FR01). Never raises (fail-open)."""
    symbols = _extract_symbols(task_description)
    br_count, br_hit, br_avail = compute_blast_radius(
        symbols, project_root=project_root, threshold=blast_radius_threshold
    )
    ch_commits, ch_authors, ch_hit, ch_avail = compute_churn(
        declared_paths, project_root=project_root, commit_threshold=churn_commit_threshold
    )
    gap, gap_hit, gap_avail = compute_precedent_gap(task_description, trw_dir=trw_dir)
    return ScoutSignals(
        blast_radius_count=br_count,
        blast_radius_hit=br_hit,
        blast_radius_available=br_avail,
        churn_commits=ch_commits,
        churn_authors=ch_authors,
        churn_hit=ch_hit,
        churn_available=ch_avail,
        precedent_gap=gap,
        precedent_gap_hit=gap_hit,
        precedent_gap_available=gap_avail,
    )


__all__ = [
    "compute_blast_radius",
    "compute_churn",
    "compute_precedent_gap",
    "compute_signals",
]
