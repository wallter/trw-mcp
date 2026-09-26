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
    """
    import os

    original_cwd = Path.cwd()
    try:
        os.chdir(target_dir)

        from trw_mcp.models.config import _reset_config, get_config
        from trw_mcp.state._store_selection import measuring_only, selected_store

        _reset_config()
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

    except Exception as exc:  # justified: boundary — auto-maintenance failure must not block update
        _logger.warning("auto_maintenance_failed", error=str(exc), target_dir=str(target_dir), exc_info=True)
        result["warnings"].append(f"Auto-maintenance skipped: {exc}")
    finally:
        os.chdir(original_cwd)
        try:
            from trw_mcp.models.config import _reset_config

            _reset_config()
        except Exception:  # justified: cleanup, config reset is best-effort during finally
            _logger.debug("auto_maintenance_config_reset_failed", exc_info=True)


def _update_git_hooks(target_dir: Path, result: dict[str, list[str]]) -> None:
    """Refresh the TRW ``post-commit`` git hook (PRD-CORE-231 FR01/FR02).

    ``force=True`` on the installed script so an updated bundled hook actually
    lands; the ``.git/hooks/post-commit`` shim itself only ever rewrites the
    marker-guarded TRW block, so a user's own hook logic survives. It writes
    outside the transaction surface, so it is an external effect: named under
    ``ran``/``would_run``, never diffed.
    """
    try:
        from trw_mcp.bootstrap._git_hooks import install_git_post_commit_hook

        hook_result = install_git_post_commit_hook(target_dir, force=True)
        result.setdefault("warnings", []).extend(hook_result["errors"])
    except Exception as exc:  # justified: fail-open, update must not abort
        logger.warning("git_hook_update_failed", error=str(exc))
        result.setdefault("warnings", []).append(f"git post-commit hook skipped: {exc}")
