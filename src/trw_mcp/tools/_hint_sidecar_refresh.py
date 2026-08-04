"""Post-commit T2 sidecar refresh planning (PRD-CORE-231-FR01).

``.trw/distill/map-cache/`` is keyed by HEAD sha, so every commit invalidates
the whole T2 tier: the PreToolUse hint hook stays armed but permanently falls
back to T1/T0 because no sidecar for the new sha exists. This module decides —
in typed, testable Python rather than shell — which files the post-commit hook
should regenerate a sidecar for.

The hook (``data/git_hooks/trw-post-commit.sh``) is a thin shell
wrapper: it asks this module for a plan and shells out to the already-shipped
``trw-distill self-improve before-edit --files-from ... --persist-sidecar`` CLI
ONCE for the whole plan. Zero trw-distill imports live here — the CLI is invoked
as an external subprocess, unchanged.

One invocation, not one per file, is load-bearing rather than an optimization:
the CLI's single-file mode persists to ``before-edit-hint-<sha>.json``, a name
carrying no per-file discriminator, so N invocations overwrite each other and
leave one artifact. Batch mode persists ``before-edit-batch-<sha>.json``, one
hint per target.

Fail-open throughout: a missing entitlement, absent git, or any error yields an
EMPTY plan, and the hook exits 0 without blocking ``git commit``.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

#: Env vars the sidecar subprocess legitimately needs. Everything else is
#: dropped — a git hook inherits the committing shell's full environment, and
#: full-env passthrough to a child process previously enabled secret
#: exfiltration via probe stdout (.claude/rules/trw-mcp-python.md).
_ENV_ALLOWLIST: tuple[str, ...] = (
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "PYTHONPATH",
    "TMPDIR",
    "TRW_CLIENT_PROFILE",
    "TRW_PROJECT_DIR",
    "VIRTUAL_ENV",
)

# ``--root`` is load-bearing: without it diff-tree emits NOTHING for a commit
# with no parent, so the very first commit in a repo would silently refresh no
# sidecars at all.
_GIT_CHANGED_FILES = ("git", "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", "HEAD")


@dataclass(frozen=True, slots=True)
class RefreshPlan:
    """What the post-commit hook should regenerate, and why it might not."""

    files: tuple[str, ...] = ()
    skipped_reason: str = ""
    truncated: bool = False

    @property
    def should_run(self) -> bool:
        """True when there is at least one file worth refreshing."""
        return bool(self.files)


@dataclass(frozen=True, slots=True)
class SubprocessSpec:
    """A single fully-resolved sidecar-refresh invocation."""

    argv: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RefreshOutcome:
    """What the refresh was asked to do, and what it demonstrably achieved.

    ``refreshed_files`` counts targets a sidecar on disk ACTUALLY describes —
    never invocations, never exit codes. The distinction is the whole point of
    this type: the CLI exits 0 on paths that leave no usable artifact for a
    given target, so a count derived from process outcomes reports success for
    files that got nothing.
    """

    plan: RefreshPlan
    refreshed_files: int = 0


def sanitized_env(source: dict[str, str]) -> dict[str, str]:
    """Project *source* onto the explicit allowlist (NFR03).

    Never ``os.environ.copy()``: a git hook runs with the committing user's
    full environment, including credentials that a third-party CLI has no
    business receiving.
    """
    return {key: source[key] for key in _ENV_ALLOWLIST if key in source}


def changed_files(repo_root: Path) -> tuple[str, ...]:
    """Repo-relative paths touched by HEAD, or ``()`` when git is unavailable."""
    try:
        completed = subprocess.run(  # noqa: S603 — fixed argv constant, no shell, no caller input
            _GIT_CHANGED_FILES,
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("sidecar_refresh_git_unavailable", exc_info=True)
        return ()
    if completed.returncode != 0:
        return ()
    return tuple(line.strip() for line in completed.stdout.splitlines() if line.strip())


def resolve_refresh_plan(
    repo_root: Path,
    *,
    enabled: bool,
    file_cap: int,
    distill_available: bool,
) -> RefreshPlan:
    """Decide which changed files get a regenerated sidecar.

    Args:
        repo_root: Repository root the commit landed in.
        enabled: ``TRWConfig.hint_sidecar_refresh_enabled`` (FR01 rollback switch).
        file_cap: ``TRWConfig.hint_sidecar_refresh_file_cap`` (NFR01 bound).
        distill_available: Whether the trw-distill CLI is importable/entitled.

    Returns:
        A :class:`RefreshPlan`. Every negative path returns an EMPTY plan with a
        named ``skipped_reason`` — the free-tier fail-open contract, unchanged.
    """
    if not enabled:
        return RefreshPlan(skipped_reason="disabled_by_config")
    if not distill_available:
        return RefreshPlan(skipped_reason="distill_unavailable")

    files = changed_files(repo_root)
    if not files:
        return RefreshPlan(skipped_reason="no_changed_files")

    capped = files[:file_cap]
    return RefreshPlan(files=capped, truncated=len(capped) < len(files))


@contextlib.contextmanager
def targets_file(files: tuple[str, ...]) -> Iterator[Path]:
    """Write *files* newline-separated to a private temp file, then remove it.

    ``--files A,B,C`` would be shorter, but the CLI splits that on commas and a
    repo-relative path may legally contain one — which would silently swap two
    nonexistent targets in for a real file, reintroducing this module's own
    defect class one level down. The newline form has no such collision for any
    path git reports unquoted.
    """
    handle, name = tempfile.mkstemp(prefix="trw-sidecar-targets-", suffix=".txt")
    path = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as sink:
            sink.write("\n".join(files) + "\n")
        yield path
    finally:
        path.unlink(missing_ok=True)


def build_subprocess_spec(
    plan: RefreshPlan,
    repo_root: Path,
    source_env: dict[str, str],
    *,
    targets_path: Path,
) -> SubprocessSpec | None:
    """Materialize the ONE invocation that covers the whole plan.

    One invocation, not one per file. ``--file`` mode persists to
    ``<cache>/before-edit-hint-<sha>.json``, a name with no per-file
    discriminator, so N single-file invocations overwrote each other and left a
    single artifact describing whichever path git emitted LAST — while the
    completion log and the post-commit receipt both reported N. ``--files-from``
    mode persists ``before-edit-batch-<sha>.json``, which carries one hint per
    target; :func:`count_refreshed_targets` reads that artifact back, and
    ``compute_before_edit_hint`` consumes it.

    Target paths travel in a file, never in argv and never through a shell, so a
    filename containing shell metacharacters remains inert.

    Returns ``None`` for an empty plan — there is nothing to invoke.
    """
    if not plan.files:
        return None
    return SubprocessSpec(
        argv=(
            "trw-distill",
            "self-improve",
            "before-edit",
            "--repo",
            str(repo_root),
            "--files-from",
            str(targets_path),
            "--persist-sidecar",
        ),
        env=sanitized_env(source_env),
    )


def _targets_described_by(payload: Any) -> set[str]:
    """Target paths a loaded sidecar payload describes (single OR batch shape)."""
    if not isinstance(payload, dict):
        return set()
    single = payload.get("target_path")
    if isinstance(single, str) and single:
        return {single}
    hints = payload.get("hints")
    if not isinstance(hints, list):
        return set()
    return {
        hint["target_path"]
        for hint in hints
        if isinstance(hint, dict) and isinstance(hint.get("target_path"), str) and hint["target_path"]
    }


_ARTIFACT_NAMES: tuple[str, ...] = ("before-edit-hint", "before-edit-batch")


def snapshot_artifact_mtimes(repo_root: Path) -> dict[str, int]:
    """``{artifact path: st_mtime_ns}`` for the sidecars, taken BEFORE a refresh.

    Without this, :func:`count_refreshed_targets` credits THIS run with any
    current-SHA artifact already on disk — one left by an earlier refresh at the
    same commit, or written by the pre-edit hook. A run whose producer failed
    outright would then still report those targets as refreshed, which is the same
    "the accounting believes the wrong thing" defect this module has now been
    through twice.
    """
    from trw_mcp.tools._sidecar_substrate import DEFAULT_CACHE_DIR_REL

    cache_dir = repo_root / DEFAULT_CACHE_DIR_REL
    seen: dict[str, int] = {}
    for artifact in _ARTIFACT_NAMES:
        for path in cache_dir.glob(f"{artifact}-*.json"):
            try:
                seen[str(path)] = path.stat().st_mtime_ns
            except OSError:
                continue
    return seen


def count_refreshed_targets(
    repo_root: Path,
    targets: tuple[str, ...],
    *,
    since: dict[str, int] | None = None,
) -> int:
    """How many of *targets* THIS refresh actually landed a sidecar for.

    This is the only honest measure of what a refresh delivered. Counting
    invocations, or their exit codes, answers a different question — commit
    0cdd60c420 added exit-code accounting to fix this very log line and the
    wrong number survived, because all N exits were genuinely 0 while N-1 of the
    artifacts had been overwritten.

    Reading the artifact back is necessary but not sufficient: an artifact that was
    already there answers "a sidecar describes this file", not "this run produced
    one". *since* — a :func:`snapshot_artifact_mtimes` map taken before the
    producer ran — closes that. An artifact whose mtime did not advance is not
    counted, so a run whose producer failed reports zero even when a valid
    same-SHA artifact is sitting in the cache.

    Passing ``since=None`` keeps the old read-back-only behaviour for callers that
    genuinely want "is a sidecar available for this file" rather than "did this run
    write one".

    Fail-closed throughout: an unreadable cache, an unresolvable HEAD, a stale-sha
    envelope, or an unchanged mtime all count ZERO. Under-reporting is visible and
    recoverable; claiming a refresh that did not land is the defect. That policy is
    also why a producer which skips a byte-identical rewrite would under-count here
    rather than over-count.
    """
    if not targets:
        return 0
    from trw_mcp.tools._sidecar_substrate import DEFAULT_CACHE_DIR_REL, load_envelope, resolve_git_sha

    sha = resolve_git_sha(repo_root)
    if sha is None:
        return 0
    cache_dir = repo_root / DEFAULT_CACHE_DIR_REL
    described: set[str] = set()
    for artifact in _ARTIFACT_NAMES:
        path = cache_dir / f"{artifact}-{sha}.json"
        if since is not None:
            try:
                current = path.stat().st_mtime_ns
            except OSError:
                continue
            if current == since.get(str(path)):
                continue
        envelope = load_envelope(path)
        if envelope is None or envelope.get("sha") != sha:
            continue
        described |= _targets_described_by(envelope.get("payload"))
    return len(set(targets) & described)


def distill_available() -> bool:
    """Whether the trw-distill sidecar feature is installed/entitled."""
    try:
        from trw_mcp.tools._sidecar_substrate import distill_installed

        return bool(distill_installed())
    except Exception:  # justified: fail-open, absence is the free-tier norm
        logger.debug("sidecar_refresh_distill_probe_failed", exc_info=True)
        return False


#: How much of a failing subprocess's stderr to keep in the maintainer log.
#: Enough to name the cause, bounded so a runaway traceback cannot flood the
#: sink. Local log file only — never a tool response, never telemetry.
_STDERR_TAIL_CHARS: int = 300


def _stderr_tail(stderr: object) -> str:
    """Bounded, decoded tail of a subprocess's stderr ('' when unavailable)."""
    if isinstance(stderr, bytes):
        text = stderr.decode("utf-8", errors="replace")
    elif isinstance(stderr, str):
        text = stderr
    else:
        return ""
    return text.strip()[-_STDERR_TAIL_CHARS:]


def run_post_commit_refresh(repo_root: Path, source_env: dict[str, str]) -> RefreshOutcome:
    """Plan and execute the post-commit sidecar refresh (hook entry point).

    Any subprocess failure or timeout is swallowed *as far as the caller is
    concerned*: this runs from a git ``post-commit`` hook, where a non-zero exit
    or a hang is far more costly than a missing sidecar (NFR02 fail-open).

    Swallowed is not the same as unrecorded, and the exit code is not the
    record. ``sidecar_refresh_complete`` reports ``planned`` alongside
    ``refreshed``, where ``refreshed`` is re-derived from the artifacts on disk
    (:func:`count_refreshed_targets`) rather than from how the process ended.
    An exit code cannot see an artifact that was written and then overwritten,
    which is exactly what used to happen; the two numbers diverging is the
    signal. The exit code is still logged, as a cause rather than as the
    verdict.

    Maintainer signal goes to ``info``/``warning``: under a default install
    ``debug`` reaches nowhere, which would delete it rather than move it
    (.claude/rules/trw-mcp-python.md).
    """
    from trw_mcp.models.config import get_config

    config = get_config()
    plan = resolve_refresh_plan(
        repo_root,
        enabled=config.hint_sidecar_refresh_enabled,
        file_cap=config.hint_sidecar_refresh_file_cap,
        distill_available=distill_available(),
    )
    if not plan.should_run:
        logger.debug("sidecar_refresh_skipped", reason=plan.skipped_reason)
        return RefreshOutcome(plan=plan)

    # Taken BEFORE the producer runs. Reading an artifact back proves a sidecar
    # describes a file; it does not prove THIS run wrote one. Without the
    # snapshot, a run whose subprocess failed still credited itself with any
    # current-SHA artifact already in the cache.
    before_mtimes = snapshot_artifact_mtimes(repo_root)

    with targets_file(plan.files) as targets_path:
        spec = build_subprocess_spec(plan, repo_root, source_env, targets_path=targets_path)
        if spec is None:  # pragma: no cover — should_run already proved files is non-empty
            return RefreshOutcome(plan=plan)
        try:
            # S603: argv is a fixed command tuple plus a temp-file path, passed
            # as a LIST (never shell=True). Target paths are not in argv at all.
            completed = subprocess.run(  # noqa: S603
                spec.argv,
                cwd=repo_root,
                env=spec.env,
                capture_output=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning(
                "sidecar_refresh_subprocess_failed",
                files=len(plan.files),
                reason=type(exc).__name__,
                error=str(exc),
            )
        else:
            if completed.returncode != 0:
                logger.warning(
                    "sidecar_refresh_subprocess_nonzero_exit",
                    files=len(plan.files),
                    returncode=completed.returncode,
                    stderr_tail=_stderr_tail(completed.stderr),
                )

    refreshed = count_refreshed_targets(repo_root, plan.files, since=before_mtimes)
    logger.info(
        "sidecar_refresh_complete",
        planned=len(plan.files),
        refreshed=refreshed,
        truncated=plan.truncated,
    )
    return RefreshOutcome(plan=plan, refreshed_files=refreshed)


__all__ = [
    "RefreshOutcome",
    "RefreshPlan",
    "SubprocessSpec",
    "build_subprocess_spec",
    "changed_files",
    "count_refreshed_targets",
    "distill_available",
    "resolve_refresh_plan",
    "run_post_commit_refresh",
    "sanitized_env",
    "targets_file",
]
