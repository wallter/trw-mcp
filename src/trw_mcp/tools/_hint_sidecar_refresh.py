"""Post-commit T2 sidecar refresh planning (PRD-CORE-231-FR01).

``.trw/distill/map-cache/`` is keyed by HEAD sha, so every commit invalidates
the whole T2 tier: the PreToolUse hint hook stays armed but permanently falls
back to T1/T0 because no sidecar for the new sha exists. This module decides —
in typed, testable Python rather than shell — which files the post-commit hook
should regenerate a sidecar for.

The hook (``data/git_hooks/trw-post-commit.sh``) is a thin shell
wrapper: it asks this module for a plan and shells out to the already-shipped
``trw-distill self-improve before-edit --persist-sidecar`` CLI. Zero trw-distill
imports live here — the CLI is invoked as an external subprocess, unchanged.

Fail-open throughout: a missing entitlement, absent git, or any error yields an
EMPTY plan, and the hook exits 0 without blocking ``git commit``.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

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


def build_subprocess_specs(
    plan: RefreshPlan,
    repo_root: Path,
    source_env: dict[str, str],
) -> tuple[SubprocessSpec, ...]:
    """Materialize one sidecar-refresh invocation per planned file.

    Paths are passed as ARGV elements, never interpolated into a shell string,
    so a filename containing shell metacharacters cannot be executed.
    """
    env = sanitized_env(source_env)
    return tuple(
        SubprocessSpec(
            argv=(
                "trw-distill",
                "self-improve",
                "before-edit",
                "--repo",
                str(repo_root),
                "--file",
                file_path,
                "--persist-sidecar",
            ),
            env=env,
        )
        for file_path in plan.files
    )


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


def run_post_commit_refresh(repo_root: Path, source_env: dict[str, str]) -> RefreshPlan:
    """Plan and execute the post-commit sidecar refresh (hook entry point).

    Every subprocess failure or timeout is swallowed *as far as the caller is
    concerned*: this runs from a git ``post-commit`` hook, where a non-zero exit
    or a hang is far more costly than a missing sidecar (NFR02 fail-open).

    Swallowed is not the same as unrecorded. Each invocation's exit status is
    inspected and counted, because ``sidecar_refresh_complete files=N`` on its
    own is indistinguishable from "all N succeeded" — and every one of them can
    fail (``trw-distill`` absent from the hook's sanitized PATH does exactly
    that) while the line still reads like success. Caller-facing correctness is
    unaffected either way: a sidecar that was never written correctly reads as
    ``sidecar_missing``. This is maintainer signal, so it goes to
    ``info``/``warning`` and stays out of the return value — under a default
    install ``debug`` reaches nowhere, which would delete the signal rather
    than move it (.claude/rules/trw-mcp-python.md).
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
        return plan

    succeeded = 0
    failed = 0
    for spec, file_path in zip(build_subprocess_specs(plan, repo_root, source_env), plan.files, strict=True):
        try:
            # S603: argv is a fixed command tuple plus git-reported paths,
            # passed as a LIST (never shell=True), so a filename containing
            # shell metacharacters is inert.
            completed = subprocess.run(  # noqa: S603
                spec.argv,
                cwd=repo_root,
                env=spec.env,
                capture_output=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            failed += 1
            logger.warning(
                "sidecar_refresh_subprocess_failed",
                file_path=file_path,
                reason=type(exc).__name__,
                error=str(exc),
            )
            continue
        if completed.returncode == 0:
            succeeded += 1
            continue
        failed += 1
        logger.warning(
            "sidecar_refresh_subprocess_nonzero_exit",
            file_path=file_path,
            returncode=completed.returncode,
            stderr_tail=_stderr_tail(completed.stderr),
        )

    logger.info(
        "sidecar_refresh_complete",
        files=len(plan.files),
        succeeded=succeeded,
        failed=failed,
        truncated=plan.truncated,
    )
    return plan


__all__ = [
    "RefreshPlan",
    "SubprocessSpec",
    "build_subprocess_specs",
    "changed_files",
    "distill_available",
    "resolve_refresh_plan",
    "run_post_commit_refresh",
    "sanitized_env",
]
