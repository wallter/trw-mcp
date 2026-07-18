"""Isolated native blocking-hook execution for candidate Git transactions."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import structlog

from trw_mcp.models.git_commit_transaction import OwnershipManifest

logger = structlog.get_logger(__name__)

_BLOCKING_HOOKS = ("pre-commit", "prepare-commit-msg", "commit-msg")

# Hard wall-clock timeout (seconds) applied to each native blocking hook. A hung
# or interactive hook -- e.g. a pre-commit that prompts, or any hook that reads
# stdin -- would otherwise block publication indefinitely, holding the isolated
# candidate context and stalling every downstream operation (publish, gc). This
# is the named, documented default; it is config-driven via
# ``TRWConfig.git_hook_timeout_seconds`` (env ``TRW_GIT_HOOK_TIMEOUT_SECONDS`` /
# ``.trw/config.yaml``) and overridable per call through
# ``run_blocking_hooks(hook_timeout_seconds=...)``.
DEFAULT_HOOK_TIMEOUT_SECONDS: float = 120.0


def _resolve_hook_timeout_seconds() -> float:
    """Resolve the per-hook wall-clock timeout from TRWConfig (config precedence).

    Uses ``getattr`` with the documented default so the guard is active today and
    honours ``TRWConfig.git_hook_timeout_seconds`` the moment that field ships,
    mirroring the ``getattr(config, "<name>_timeout_seconds", <default>)`` pattern
    used by the sync client. Falls back to the default on any config-load failure
    so hook execution never depends on config being loadable.
    """
    try:
        from trw_mcp.models.config import get_config

        return float(getattr(get_config(), "git_hook_timeout_seconds", DEFAULT_HOOK_TIMEOUT_SECONDS))
    except Exception:  # trw:intentional never let config loading break the fail-closed guard
        return DEFAULT_HOOK_TIMEOUT_SECONDS


class GitRunner(Protocol):
    """Typed seam for the transaction module's fixed-argument Git runner."""

    def __call__(self, repo_root: Path, *args: str, env: dict[str, str] | None = None) -> str: ...


def _snapshot_shared_state(repo_root: Path, *, git: GitRunner, error_type: type[RuntimeError]) -> tuple[str, ...]:
    """Capture shared refs, checkout, index, and complete worktree state.

    Semantic index observations use a temporary byte-for-byte copy because Git
    status may refresh the real index even with optional locks disabled.
    """
    checked_out_ref = git(repo_root, "symbolic-ref", "-q", "HEAD").strip() or "(detached)"
    head_oid = git(repo_root, "rev-parse", "HEAD").strip()
    all_refs = git(repo_root, "for-each-ref", "--format=%(refname)%09%(objectname)%09%(symref)")
    index_path = Path(git(repo_root, "rev-parse", "--path-format=absolute", "--git-path", "index").strip())
    index_stat = index_path.stat() if index_path.exists() else None
    index_bytes = index_path.read_bytes() if index_stat is not None else None
    index_digest = "sha256:" + hashlib.sha256(index_bytes).hexdigest() if index_bytes is not None else "absent"

    with tempfile.TemporaryDirectory(prefix="trw-hook-observe-") as tmp_dir:
        observed_index = Path(tmp_dir) / "index"
        observation_env = {"GIT_INDEX_FILE": str(observed_index), "GIT_OPTIONAL_LOCKS": "0"}
        if index_bytes is None:
            git(repo_root, "read-tree", "--empty", env=observation_env)
            index_tree = "absent"
        else:
            observed_index.write_bytes(index_bytes)
            if index_stat is None:  # defensive guard for the paired snapshot values
                raise error_type("shared index disappeared while preparing hook invariants")
            # Preserve Git's racy-clean timestamp semantics across snapshots.
            os.utime(observed_index, ns=(index_stat.st_atime_ns, index_stat.st_mtime_ns))
            index_tree = git(repo_root, "write-tree", env=observation_env).strip()
        complete_porcelain = git(
            repo_root,
            "status",
            "--porcelain=v2",
            "--untracked-files=all",
            env=observation_env,
        )
    return checked_out_ref, head_oid, all_refs, index_digest, index_tree, complete_porcelain


def run_blocking_hooks(
    repo_root: Path,
    manifest: OwnershipManifest,
    message: str,
    *,
    git: GitRunner,
    mark_mutation: Callable[[Path, str], None],
    error_type: type[RuntimeError],
    hook_timeout_seconds: float | None = None,
) -> None:
    """Run native blocking hooks entirely inside a disposable Git context.

    Each hook runs with a hard wall-clock ``timeout`` and a closed stdin so a
    hung or interactive hook cannot block publication forever. ``timeout`` is
    resolved from ``TRWConfig`` (see ``_resolve_hook_timeout_seconds``) unless an
    explicit ``hook_timeout_seconds`` is supplied. A hook that exceeds the
    timeout FAILS CLOSED: publication is blocked via ``error_type``.
    """
    timeout_seconds = hook_timeout_seconds if hook_timeout_seconds is not None else _resolve_hook_timeout_seconds()
    hooks_dir = Path(git(repo_root, "rev-parse", "--path-format=absolute", "--git-path", "hooks").strip())
    objects_dir = Path(git(repo_root, "rev-parse", "--path-format=absolute", "--git-path", "objects").strip())
    before_shared = _snapshot_shared_state(repo_root, git=git, error_type=error_type)

    try:
        with tempfile.TemporaryDirectory(prefix="trw-hooks-") as tmp_dir:
            context_root = Path(tmp_dir) / "worktree"
            context_root.mkdir()
            private_index = Path(tmp_dir) / "index"
            isolated_git_dir = Path(tmp_dir) / "git"

            index_env = {"GIT_INDEX_FILE": str(private_index)}
            git(repo_root, "read-tree", manifest.parent_oid, env=index_env)
            for path in manifest.owned_paths:
                if manifest.path_digests.get(path) == "":
                    git(repo_root, "update-index", "--force-remove", "--", path, env=index_env)
                else:
                    git(repo_root, "add", "--force", "--", path, env=index_env)

            candidate_tree = git(repo_root, "write-tree", env=index_env).strip()
            git(repo_root, "init", "--bare", "--quiet", str(isolated_git_dir))
            alternates = isolated_git_dir / "objects" / "info" / "alternates"
            alternates.parent.mkdir(parents=True, exist_ok=True)
            alternates.write_text(str(objects_dir.resolve()) + "\n", encoding="utf-8")
            hook_git_env = {
                "GIT_DIR": str(isolated_git_dir),
                "GIT_INDEX_FILE": str(private_index),
                "GIT_WORK_TREE": str(context_root),
            }
            checked_out_ref = before_shared[0]
            if checked_out_ref == "(detached)":
                git(repo_root, "update-ref", "--no-deref", "HEAD", manifest.parent_oid, env=hook_git_env)
            else:
                git(repo_root, "update-ref", checked_out_ref, manifest.parent_oid, env=hook_git_env)
                git(repo_root, "symbolic-ref", "HEAD", checked_out_ref, env=hook_git_env)
            git(repo_root, "checkout-index", "--all", "--force", env=hook_git_env)
            baseline_status = git(
                repo_root,
                "--no-optional-locks",
                "status",
                "--porcelain=v2",
                "--untracked-files=all",
                env=hook_git_env,
            )

            message_file = Path(tmp_dir) / "COMMIT_EDITMSG"
            message_file.write_text(message, encoding="utf-8")
            os.chmod(message_file, 0o600)
            hook_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
            hook_env.update(hook_git_env)

            for hook in _BLOCKING_HOOKS:
                hook_path = hooks_dir / hook
                if not hook_path.exists() or not os.access(hook_path, os.X_OK):
                    continue
                args = [str(hook_path)] if hook == "pre-commit" else [str(hook_path), str(message_file)]
                try:
                    result = subprocess.run(  # noqa: S603
                        args,
                        cwd=str(context_root),
                        capture_output=True,
                        text=True,
                        env=hook_env,
                        # Fail closed on a hung/interactive hook: bound the wall
                        # clock and never inherit stdin (a hook that reads stdin
                        # gets immediate EOF instead of blocking on the terminal).
                        timeout=timeout_seconds,
                        stdin=subprocess.DEVNULL,
                    )
                except subprocess.TimeoutExpired as exc:
                    logger.warning(
                        "git_blocking_hook_timeout",
                        hook=hook,
                        timeout_seconds=timeout_seconds,
                        transaction_id=manifest.transaction_id,
                    )
                    raise error_type(
                        f"publication blocked: {hook} hook exceeded {timeout_seconds:g}s timeout "
                        "(possible hang or interactive prompt)"
                    ) from exc
                if result.returncode != 0:
                    raise error_type(f"publication blocked: {hook} hook failed: {result.stderr.strip()[:200]}")

            after_tree = git(repo_root, "write-tree", env=hook_git_env).strip()
            after_status = git(
                repo_root,
                "--no-optional-locks",
                "status",
                "--porcelain=v2",
                "--untracked-files=all",
                env=hook_git_env,
            )
            if (
                after_tree != candidate_tree
                or after_status != baseline_status
                or message_file.read_text(encoding="utf-8") != message
            ):
                mark_mutation(repo_root, manifest.transaction_id)
                raise error_type(
                    "publication blocked: a hook mutated the candidate context (isolated index/worktree) — "
                    "re-review required"
                )
    finally:
        after_shared = _snapshot_shared_state(repo_root, git=git, error_type=error_type)
        if after_shared != before_shared:
            raise error_type(
                "publication blocked: a hook changed shared refs/HEAD, index tree/bytes, or worktree state"
            )
