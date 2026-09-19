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
    """Run auto-maintenance (embeddings backfill, stale run close) after update.

    All operations are local (no API key required).  Fail-open — errors are
    logged as warnings but never break the update.
    """
    import concurrent.futures
    import os

    original_cwd = Path.cwd()
    try:
        os.chdir(target_dir)

        from trw_mcp.models.config import _reset_config, get_config
        from trw_mcp.state._memory_connection import (
            backfill_embeddings,
            check_embeddings_status,
        )

        _reset_config()
        get_config()
        trw_dir = target_dir / ".trw"

        # Check embeddings status and backfill if available
        emb_status = check_embeddings_status()
        if emb_status.get("enabled") and emb_status.get("available"):
            if on_progress:
                on_progress("Phase", "Backfilling embeddings (this may take 30-60s on first run)...")
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                future = pool.submit(backfill_embeddings, trw_dir)
                backfill = future.result(timeout=timeout)
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
            embedded = backfill.get("embedded", 0)
            if embedded > 0:
                result.setdefault("info", []).append(f"Embeddings backfilled: {embedded} entries")
            _migrate_stale_vectors(trw_dir, result, timeout, on_progress)
            from trw_mcp.state._embedding_space import stale_space_warning

            if stale := stale_space_warning(trw_dir):
                result["warnings"].append(stale)
        elif emb_status.get("enabled") and not emb_status.get("available"):
            hint = emb_status.get("advisory", "pip install sentence-transformers")
            result["warnings"].append(f"Embeddings enabled but unavailable \u2014 {hint}")

    except concurrent.futures.TimeoutError:
        result["warnings"].append(
            f"Embeddings backfill timed out ({timeout}s) \u2014 will complete on next trw_session_start()"
        )
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


def _migrate_stale_vectors(
    trw_dir: Path, result: dict[str, list[str]], timeout: int, on_progress: ProgressCallback
) -> None:
    """Re-embed vectors outside the configured model's space, within *timeout*.

    The same bounded, resumable, lock-guarded run ``trw_session_start`` starts
    in the background (``_embedding_migration``); here it runs in the
    foreground because update-project is an explicit maintenance step. What
    does not fit in the budget continues at the next session start.
    """
    from trw_mcp.state._embedding_migration import Budget
    from trw_mcp.state._embedding_migration_schedule import migrate_now
    from trw_mcp.state._memory_connection import get_embedder

    provider = get_embedder()
    if provider is None:
        return
    if on_progress:
        on_progress("Phase", "Re-embedding stored vectors into the configured model's space...")
    try:
        report = migrate_now(trw_dir, provider, budget=Budget(seconds=timeout * 0.75, rows=1_000_000))
    except Exception as exc:  # justified: boundary, a refused store (schema, RBAC, target) must not abort the update
        _logger.warning("embedding_migration_failed", reason="store_refused", error=str(exc), exc_info=True)
        result["warnings"].append(f"Embedding migration skipped: {exc}")
        return
    if report.get("run") == "busy":
        result.setdefault("info", []).append("Embedding migration is running in another session")
    elif report.get("run") == "worked":
        repaired, total = report.get("repaired", 0), report.get("total", 0)
        tail = "complete" if report.get("status") == "complete" else "continues at the next session start"
        result.setdefault("info", []).append(
            f"Re-embedded {repaired}/{total} stored vectors to {report.get('model')} ({tail})"
        )


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
