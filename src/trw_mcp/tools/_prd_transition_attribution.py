"""Attribute PRD transitions using this worktree's HEAD reflog, never edit events.

Dirty/index status additions count. Committed additions count only for commits
created or replayed here during this run; every owned merge must change the status
against EVERY parent. Missing reflog coverage or unavailable history retains all
base-diff candidates (none when no status changed). Every git command runs in the
project root, is read-only, and history is read in one batch.

Known hole: a flip committed in another worktree or clone and brought in by a
pure HEAD movement is not attributed to this session. Reflog ownership is at
worktree granularity, not an identity claim about individual agents sharing it.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

# Only known pure movements are excluded; unknown actions fail closed.
_MOVEMENT_ACTIONS = frozenset({"checkout", "reset", "clone", "branch"})
_MOVEMENT_SUFFIXES = ("(start)", "(finish)", "(abort)")


class TransitionEvidenceUnavailable(RuntimeError):
    """Git could not establish the candidate transition set."""


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- fixed read-only subcommands, no shell
        ["git", *args],  # noqa: S607 -- git is the repository VCS executable
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _checked_git(root: Path, *args: str) -> str:
    result = _git(root, *args)
    if result.returncode:
        raise TransitionEvidenceUnavailable("git could not establish PRD transition attribution")
    return result.stdout


def _reflog_commits(root: Path, base: str, start: int | None) -> set[str]:
    """Read raw HEAD entries so coverage includes old as well as new object ids."""
    path = Path(_checked_git(root, "rev-parse", "--git-path", "logs/HEAD").strip())
    text = (root / path).read_text(encoding="utf-8")
    owned: set[str] = set()
    covered = False
    for line in text.splitlines():
        metadata, _, action_text = line.partition("\t")
        old, new, _identity = metadata.split(" ", 2)
        covered |= base in (old, new)
        # Coverage is deliberately independent of the ownership time window.
        timestamp = int(metadata.rsplit(" ", 2)[-2])
        if start is not None and timestamp < start:
            continue
        action, _, message = action_text.partition(":")
        if (
            action
            and action not in _MOVEMENT_ACTIONS
            and not action.endswith(_MOVEMENT_SUFFIXES)
            and message.strip().lower() != "fast-forward"
        ):
            owned.add(new)
    if not covered:
        raise TransitionEvidenceUnavailable("HEAD reflog does not cover the run base")
    return owned


def _committed_transitions(root: Path, base: str, start: int | None) -> set[str]:
    from trw_mcp.tools._prd_transition_gate import PRDS_DIR, detect_status_transitions

    base_sha = _checked_git(root, "rev-parse", "--verify", base).strip()
    owned = _reflog_commits(root, base_sha, start)
    common = _checked_git(root, "merge-base", base_sha, "HEAD").strip()
    # Separate merge diffs allow intersection across *all* parents, including
    # octopus merges. Full history prevents path simplification hiding parents.
    history = _checked_git(
        root,
        "log",
        "--full-history",
        "--format=%x1e%H %P",
        "--patch",
        "--unified=0",
        "--no-renames",
        "--no-ext-diff",
        "--no-textconv",
        "--diff-merges=separate",
        f"{common}..HEAD",
        "--",
        PRDS_DIR,
    )
    attributed: set[str] = set()
    merge_diffs: dict[str, list[set[str]]] = {}
    parent_counts: dict[str, int] = {}
    for record in history.split("\x1e"):
        if not record.strip():
            continue
        header, _, patch = record.partition("\n")
        sha, *parents = header.split()
        if sha not in owned:
            continue
        transitions = set(detect_status_transitions(patch))
        if len(parents) <= 1:
            attributed.update(transitions)
        else:
            parent_counts[sha] = len(parents)
            merge_diffs.setdefault(sha, []).append(transitions)
    for sha, diffs in merge_diffs.items():
        # Git may omit an empty parent diff: then no status differs from every
        # parent, so this merge cannot attribute a transition.
        if len(diffs) == parent_counts[sha]:
            attributed.update(set.intersection(*diffs))
    # History explains ownership, but only net transitions in HEAD count.
    candidates = detect_status_transitions(_checked_git(root, "diff", base_sha, "HEAD", "--", PRDS_DIR))
    return attributed.intersection(candidates)


def _timestamp(value: object) -> int | None:
    """Require a timezone; retain the whole start second at git's precision."""
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(stamp.timestamp()) if stamp.tzinfo is not None else None
    except (
        ValueError,
        OverflowError,
        OSError,
    ):  # trw-fail-silent-allow: no start time; the caller logs and keeps ownership unbounded
        return None


def _run_start(run: Path, run_data: dict[str, object]) -> int | None:
    """Prefer recorded creation, then the generated run id, then the first event.

    CLI scaffolding writes created_at; MCP init replaces that scaffold but keeps
    its timestamped directory. File mtimes and later events are not start times.
    """
    start = _timestamp(run_data.get("created_at"))
    source = "run.yaml:created_at"
    if start is None:
        source = "run_directory"
        match = re.match(r"^(\d{8}T\d{6}Z)-", run.name)
        if match:
            try:
                start = int(datetime.strptime(match[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp())
            except ValueError:  # trw-fail-silent-allow: invalid directory timestamp; the first event is tried next
                pass
    if start is None:
        source = "first_event:ts"
        try:
            with (run / "meta/events.jsonl").open(encoding="utf-8") as stream:
                event = json.loads(stream.readline())
            if isinstance(event, dict):
                start = _timestamp(event.get("ts"))
        except (
            OSError,
            UnicodeError,
            ValueError,
        ):  # trw-fail-silent-allow: no start evidence; logged below, every entry stays eligible
            pass
    if start is None:
        logger.warning("prd_transition_start_unavailable", run=str(run), outcome="no_time_bound")
    else:
        logger.debug("prd_transition_start", run=str(run), source=source, timestamp=start)
    return start


def _has_history(root: Path) -> bool:
    """Whether *root* is inside a git repository that has a commit.

    Without one there is no status diff to attribute and nothing this check can certify; that was never a
    block. Whether a repository exists is read from the file system, so a repository whose git cannot be
    run is still a repository and its faults still block.
    """
    if not any((directory / ".git").exists() for directory in (root, *root.parents)):
        return False
    try:
        return _git(root, "rev-parse", "-q", "--verify", "HEAD^{commit}").returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        raise TransitionEvidenceUnavailable("git could not be run in this repository") from exc


def attributed_transitions(run: Path, run_data: dict[str, object]) -> list[str]:
    """Return owned status additions; retain candidates when git cannot prove ownership.

    Event edit claims have no role: missing reflog coverage and history faults
    use the same conservative base-candidate fallback. A missing base preserves
    the existing HEAD/index-only behavior.
    """
    from trw_mcp.state._paths import resolve_project_root
    from trw_mcp.tools._prd_transition_gate import PRDS_DIR, _prd_status_diff, _run_base_ref, detect_status_transitions

    base = _run_base_ref(run_data)
    root = resolve_project_root()
    if not _has_history(root):
        return []
    try:
        diff = _prd_status_diff(base)
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        raise TransitionEvidenceUnavailable("PRD diff unavailable; transition certification cannot proceed") from exc
    if diff is None:
        raise TransitionEvidenceUnavailable("PRD diff unavailable; transition certification cannot proceed")
    candidates = detect_status_transitions(diff)
    attributed: set[str] = set()
    try:
        attributed.update(detect_status_transitions(_checked_git(root, "diff", "HEAD", "--", PRDS_DIR)))
        attributed.update(detect_status_transitions(_checked_git(root, "diff", "--cached", "HEAD", "--", PRDS_DIR)))
        if base:
            attributed.update(_committed_transitions(root, base, _run_start(run, run_data)))
        return sorted(attributed)
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError, TransitionEvidenceUnavailable):
        logger.warning(
            "prd_transition_attribution_unavailable", run=str(run), outcome="retain_candidates", exc_info=True
        )
        # Ownership is unknown, so every status change since the base counts (the behaviour before attribution
        # existed). When there is none, there is nothing to certify: an unreadable reflog alone never blocks.
        return sorted(set(candidates) | attributed)
