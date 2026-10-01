"""Update-project effects that write OUTSIDE the transaction surface.

PRD-INFRA-190 FR02: these run only on a real update, after the managed-file
transaction commits. A dry run names them under ``would_run``; a real run
names them under ``ran``. None of them is part of the changed-file diff.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from ._utils import ProgressCallback

logger = structlog.get_logger(__name__)
_logger = logger


def _run_auto_maintenance(
    target_dir: Path,
    result: dict[str, list[str]],
    timeout: int = 120,
    on_progress: ProgressCallback = None,
) -> None:
    """Run auto-maintenance after update: warn when the memory daemon cannot encode.

    All operations are local (no API key required).  Fail-open — errors are
    logged as warnings but never break the update.

    *target_dir* is bound as "the project" (``state._project_root_binding``), so
    get_config() reads its config; this process's cwd and cached config, which
    other threads share, are never touched (B71-118).
    """
    from trw_mcp.state._project_root_binding import installing_into
    from trw_mcp.state._store_selection import DaemonBudgetExhaustedError

    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.state._store_selection import measuring_only, selected_store

        with installing_into(target_dir):
            # The daemon holds the model (PRD-CORE-302 FR05), so its memory_status answers
            # whether this checkout's recall can encode. PRD-CORE-298 FR01: nothing here
            # opens the checkout's own `.trw/memory/memory.db`, not even to explain a
            # missing pin, which update-project has already written.
            if get_config().embeddings_enabled:
                with measuring_only():
                    store, namespace = selected_store(target_dir / ".trw")
                embedder = store.embedder_status(namespace)
                if not embedder.get("available"):
                    fix = f" \u2014 run: {embedder['fix']}" if embedder.get("fix") else ""
                    result["warnings"].append(f"Memory daemon cannot encode: {embedder.get('reason')}{fix}")

    except DaemonBudgetExhaustedError as exc:  # FB-INSTALL-07: a just-restarted daemon is loading its model
        _logger.warning("auto_maintenance_daemon_warming", error=str(exc), target_dir=str(target_dir))
        result["warnings"].append(
            "The memory daemon did not answer within the install's budget: it may still be loading its embedding model "
            "(typically about 30 s after a restart), so this update continued without recalled learnings and without the "
            "embedding check, and the first recall will be slow. Run `trw-mcp doctor` in a minute to confirm."
        )
    except Exception as exc:  # justified: boundary — auto-maintenance failure must not block update
        _logger.warning("auto_maintenance_failed", error=str(exc), target_dir=str(target_dir), exc_info=True)
        result["warnings"].append(f"Auto-maintenance skipped: {exc}")


def write_hook_interpreter(target_dir: Path, result: dict[str, list[str]]) -> None:
    """Record the interpreter running trw-mcp as the one the bundled hooks start (PRD-FIX-155).

    Four hooks read this file and nothing wrote it, so they fell back to bare
    ``python3``, which usually cannot import trw_mcp. The interpreter running
    this code imports trw_mcp by construction. init-project and update-project
    both rewrite it, so a moved venv is followed, including in a checkout with no
    git repo (the client hint hooks read it too). The one writer is
    :func:`record_hook_interpreter` (PRD-FIX-156: symlink-guarded, atomic,
    unchanged file left alone); this adapter reports its refusal as a warning.
    """
    from trw_mcp.bootstrap._hook_interpreter import record_hook_interpreter

    try:
        record_hook_interpreter(target_dir)
    except (OSError, ValueError) as exc:
        result.setdefault("warnings", []).append(f"hook interpreter not recorded: {exc}")


def _update_git_hooks(target_dir: Path, result: dict[str, list[str]]) -> None:
    """Refresh the TRW ``post-commit`` git hook (PRD-CORE-231 FR01/FR02) and the hooks' interpreter.

    ``force=True`` on the installed script so an updated bundled hook actually
    lands; the ``.git/hooks/post-commit`` shim itself only ever rewrites the
    marker-guarded TRW block, so a user's own hook logic survives. It writes
    outside the transaction surface, so it is an external effect: named under
    ``ran``/``would_run``, never diffed. The interpreter pointer every hook
    reads is its own effect, ``hook_interpreter`` (PRD-FIX-155).
    """
    write_hook_interpreter(target_dir, result)
    try:
        from trw_mcp.bootstrap._git_hooks import install_git_post_commit_hook

        hook_result = install_git_post_commit_hook(target_dir, force=True)
        result.setdefault("warnings", []).extend(hook_result["errors"])
    except Exception as exc:  # justified: fail-open, update must not abort
        logger.warning("git_hook_update_failed", error=str(exc))
        result.setdefault("warnings", []).append(f"git post-commit hook skipped: {exc}")
