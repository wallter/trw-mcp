"""Session changelog builder (PRD-LOCAL-049).

Belongs to the ``trw_mcp.state.session_changelog`` surface; the public
symbols (``build_session_changelog``, ``write_session_changelog``,
``SessionChangelogResult``, ``detect_package_changelog_advisory``) are
re-exported by :mod:`trw_mcp.tools._ceremony_deliver_steps` (deliver step)
and by the ``session-changelog`` CLI subcommand.

The builder is evidence-derived (FR02): it reads durable run events
(``meta/events.jsonl``), the review summary (``meta/review.yaml``), the
project build status (``.trw/context/build-status.yaml``), and a read-only
``git log``/``git status`` range. Every optional input degrades to an
explicit ``unknown``/empty section rather than failing — the builder NEVER
raises into deliver (FR02 fail-open). Git access is strictly read-only; the
builder never stages or commits (NFR / Non-Goal).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from ruamel.yaml import YAML

from trw_mcp.state import _paths
from trw_mcp.state._helpers import read_jsonl_resilient
from trw_mcp.state._session_changelog_render import (
    PackageChangelogCoverage as PackageChangelogCoverage,
)
from trw_mcp.state._session_changelog_render import (
    render_markdown as _render_markdown,
)
from trw_mcp.state._session_changelog_render import (
    render_minimal as _render_minimal,
)

logger = structlog.get_logger(__name__)

SESSION_CHANGELOG_FILENAME = "session-changelog.md"

# Privacy guard (NFR): never surface secret-looking commit/file paths verbatim.
_SECRETISH_RE = re.compile(r"(secret|token|password|credential|private[_-]?key|api[_-]?key)", re.IGNORECASE)

# Package roots that map a changed file to its owning package for the
# files-changed-by-package section and the changelog advisory. The repo root
# ("." == top-level files) is the implicit fallback bucket.
_GIT_TIMEOUT_SECONDS = 8


@dataclass
class SessionChangelogResult:
    """Structured result of building a session changelog (FR01/FR02/FR03)."""

    markdown: str
    run_path: str
    commits: list[dict[str, str]] = field(default_factory=list)
    changed_files_by_package: dict[str, list[str]] = field(default_factory=dict)
    has_commits: bool = False
    review_present: bool = False
    build_present: bool = False
    learnings_recorded: int = 0
    package_changelog_advisory: list[PackageChangelogCoverage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _git(args: list[str], cwd: Path) -> str | None:
    """Run a read-only git command; return stdout or None on any failure.

    Fail-open: a missing git binary, non-git tree, or timeout returns None so
    the builder degrades to an explicit ``unknown`` section.
    """
    git_executable = shutil.which("git")
    if git_executable is None:
        return None
    try:
        return subprocess.run(  # noqa: S603
            [git_executable, *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None


def _git_root(project_root: Path) -> Path | None:
    out = _git(["rev-parse", "--show-toplevel"], project_root)
    if out is None:
        return None
    root = out.strip()
    return Path(root) if root else None


def _read_yaml_safe(path: Path) -> dict[str, object] | None:
    """Read a YAML mapping with the safe loader; None when absent/unreadable.

    Fail-open — never raises. Mirrors the ``YAML(typ="safe")`` convention used
    across ``state/`` reads.
    """
    if not path.exists():
        return None
    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except Exception:  # justified: fail-open — changelog must never raise on a bad YAML
        logger.debug("session_changelog_yaml_read_failed", path=str(path), exc_info=True)
        return None
    return data if isinstance(data, dict) else None


def _collect_commits(run_events: list[dict[str, object]], git_root: Path | None) -> list[dict[str, str]]:
    """Collect commits made during the session via a read-only ``git log`` range.

    The range upper bound is HEAD; the lower bound is the commit recorded in the
    earliest ``trw_init``/session-start event when available, else the most
    recent commits are not derivable without a baseline so an empty list is
    returned (the report then states "no commits recorded this session").
    """
    if git_root is None:
        return []
    baseline = _session_baseline_sha(run_events, git_root)
    if baseline is None:
        return []
    log_range = f"{baseline}..HEAD"
    out = _git(["log", "--no-merges", "--pretty=format:%H%x1f%s%x1f%an%x1f%aI", log_range], git_root)
    if not out:
        return []
    commits: list[dict[str, str]] = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        sha, subject, author, date = parts
        if _SECRETISH_RE.search(subject):
            subject = "[redacted commit subject]"
        commits.append({"sha": sha[:12], "subject": subject.strip(), "author": author.strip(), "date": date.strip()})
    return commits


def _session_baseline_sha(run_events: list[dict[str, object]], git_root: Path) -> str | None:
    """Resolve the git SHA at session start from the earliest durable event.

    Looks for an explicit ``head_sha``/``baseline_sha`` payload on the first
    event; otherwise falls back to the commit reachable from HEAD at the first
    event's timestamp via ``git log --until``. Returns None when no baseline can
    be derived (degrades to "no commits recorded").
    """
    for event in run_events:
        for key in ("head_sha", "baseline_sha", "git_head"):
            raw = event.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    # Timestamp fallback: the commit HEAD pointed at when the session began.
    first_ts = next((str(e.get("ts", "")).strip() for e in run_events if e.get("ts")), "")
    if not first_ts:
        return None
    out = _git(["log", "-1", "--until", first_ts, "--pretty=format:%H", "HEAD"], git_root)
    if not out or not out.strip():
        return None
    return out.strip()


def _collect_changed_files(git_root: Path | None) -> tuple[dict[str, list[str]], list[str]]:
    """Group currently-changed (uncommitted) files by package root.

    Returns ``(by_package, warnings)``. Uncommitted/working-tree changes are
    reported separately from committed work (truthfulness NFR). Fail-open:
    git unavailable yields an empty map with a warning.
    """
    warnings: list[str] = []
    if git_root is None:
        warnings.append("git unavailable; changed-files section is empty")
        return {}, warnings
    out = _git(["status", "--porcelain=v1", "--untracked-files=all", "-z"], git_root)
    if out is None:
        warnings.append("git status unavailable; changed-files section is empty")
        return {}, warnings
    by_package: dict[str, list[str]] = {}
    for path in _parse_porcelain_paths(out):
        if _SECRETISH_RE.search(path):
            warnings.append("a changed path was hidden by the privacy filter")
            continue
        bucket = _package_root_of(path)
        by_package.setdefault(bucket, []).append(path)
    return by_package, warnings


def _parse_porcelain_paths(raw: str) -> list[str]:
    """Extract file paths from ``git status --porcelain=v1 -z`` output."""
    parts = [part for part in raw.split("\0") if part]
    paths: list[str] = []
    index = 0
    while index < len(parts):
        entry = parts[index]
        code = entry[:2]
        path = entry[3:]
        if code.startswith(("R", "C")):
            # rename/copy: the new path is the FOLLOWING null-separated token.
            index += 1
            if index < len(parts):
                path = parts[index]
        if path:
            paths.append(path)
        index += 1
    return paths


def _package_root_of(path: str) -> str:
    """Map a repo-relative path to its owning package root (top dir or '.')."""
    head = path.split("/", maxsplit=1)
    if len(head) == 1:
        return "."
    return head[0]


def detect_package_changelog_advisory(
    by_package: dict[str, list[str]],
    git_root: Path | None,
    *,
    changelog_filename: str = "CHANGELOG.md",
) -> list[PackageChangelogCoverage]:
    """FR03 — advisory package CHANGELOG.md coverage.

    For each changed package root, find the nearest ``CHANGELOG.md`` (the
    package root's own, else the repo root's). Coverage is "updated" when that
    changelog file is itself among the changed paths. Projects without any
    changelog produce ``changelog_path=None`` + ``changelog_updated=False`` and
    NO failure — v1 is advisory-only.
    """
    coverage: list[PackageChangelogCoverage] = []
    if git_root is None:
        return coverage
    all_changed = {p for paths in by_package.values() for p in paths}
    for package_root, paths in sorted(by_package.items()):
        changelog_rel = _nearest_changelog(package_root, git_root, changelog_filename)
        updated = bool(changelog_rel and changelog_rel in all_changed)
        coverage.append(
            PackageChangelogCoverage(
                package_root=package_root,
                changed_files=len(paths),
                changelog_path=changelog_rel,
                changelog_updated=updated,
            )
        )
    return coverage


def _nearest_changelog(package_root: str, git_root: Path, changelog_filename: str) -> str | None:
    """Return the repo-relative path of the nearest CHANGELOG, or None."""
    candidates: list[str] = []
    if package_root != ".":
        candidates.append(f"{package_root}/{changelog_filename}")
    candidates.append(changelog_filename)
    for rel in candidates:
        if (git_root / rel).is_file():
            return rel
    return None


def build_session_changelog(
    run_path: Path,
    trw_dir: Path,
    *,
    changelog_filename: str = "CHANGELOG.md",
    changelog_advisory_enabled: bool = False,
) -> SessionChangelogResult:
    """Build the session changelog markdown + structured metadata (FR01/FR02).

    Evidence-derived and fail-open: every optional input that is missing
    degrades to an explicit ``unknown``/empty section. This function never
    raises — a catastrophic internal failure still returns a minimal result
    with a warning so the deliver step stays non-blocking.
    """
    warnings: list[str] = []
    try:
        return _build_session_changelog_inner(
            run_path,
            trw_dir,
            changelog_filename=changelog_filename,
            changelog_advisory_enabled=changelog_advisory_enabled,
            warnings=warnings,
        )
    except Exception as exc:  # justified: fail-open — builder must never block deliver
        logger.warning("session_changelog_build_failed", error=str(exc), exc_info=True)
        warnings.append(f"changelog build degraded: {exc}")
        markdown = _render_minimal(run_path, warnings)
        return SessionChangelogResult(markdown=markdown, run_path=str(run_path), warnings=warnings)


def _build_session_changelog_inner(
    run_path: Path,
    trw_dir: Path,
    *,
    changelog_filename: str,
    changelog_advisory_enabled: bool,
    warnings: list[str],
) -> SessionChangelogResult:
    meta = run_path / "meta"
    events = read_jsonl_resilient(meta / "events.jsonl")
    review = _read_yaml_safe(meta / "review.yaml")
    build = _read_yaml_safe(trw_dir / "context" / "build-status.yaml")

    project_root = _paths.resolve_project_root()
    git_root = _git_root(project_root)

    commits = _collect_commits(events, git_root)
    by_package, changed_warnings = _collect_changed_files(git_root)
    warnings.extend(changed_warnings)

    learnings_recorded = sum(1 for e in events if str(e.get("event", "")) in {"trw_learn", "learning_recorded"})

    advisory: list[PackageChangelogCoverage] = []
    if changelog_advisory_enabled:
        advisory = detect_package_changelog_advisory(by_package, git_root, changelog_filename=changelog_filename)

    markdown = _render_markdown(
        run_path=run_path,
        commits=commits,
        by_package=by_package,
        review=review,
        build=build,
        events=events,
        learnings_recorded=learnings_recorded,
        advisory=advisory,
        advisory_enabled=changelog_advisory_enabled,
        warnings=warnings,
    )
    return SessionChangelogResult(
        markdown=markdown,
        run_path=str(run_path),
        commits=commits,
        changed_files_by_package=by_package,
        has_commits=bool(commits),
        review_present=review is not None,
        build_present=build is not None,
        learnings_recorded=learnings_recorded,
        package_changelog_advisory=advisory,
        warnings=warnings,
    )


def write_session_changelog(
    run_path: Path,
    trw_dir: Path,
    *,
    changelog_filename: str = "CHANGELOG.md",
    changelog_advisory_enabled: bool = False,
) -> tuple[Path, SessionChangelogResult]:
    """Build and persist the session changelog to ``reports/`` (FR01/FR04).

    Returns ``(report_path, result)``. Creates the ``reports/`` directory if
    absent. Fail-open at the build layer; a write failure is the caller's
    responsibility to wrap (the deliver step wraps it).
    """
    result = build_session_changelog(
        run_path,
        trw_dir,
        changelog_filename=changelog_filename,
        changelog_advisory_enabled=changelog_advisory_enabled,
    )
    reports_dir = run_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / SESSION_CHANGELOG_FILENAME
    report_path.write_text(result.markdown, encoding="utf-8")
    logger.info(
        "session_changelog_written",
        path=str(report_path),
        commits=len(result.commits),
        changed_packages=len(result.changed_files_by_package),
    )
    return report_path, result
