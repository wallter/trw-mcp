"""Recall context builder — pure state-layer logic.

PRD-CORE-146 architectural follow-up: relocated from ``tools/_recall_impl.py``
so ``state/`` callers (e.g. ``ceremony_nudge``, ``_ceremony_status``) no longer
need the importlib workaround that bypassed the state→tools layer boundary
lint.

The function builds a ``RecallContext`` from:
  * current ceremony phase (state/_paths)
  * git HEAD diff (best-effort)
  * config-resolved client profile + model family (models/config)
  * active-run PRD knowledge ids (state/_paths + state/persistence)
  * local bandit-params intel cache (sync/cache)

It has no dependency on anything under ``trw_mcp/tools/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog

from trw_mcp.scoring._recall import RecallContext
from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from trw_mcp.scoring._recall import _IntelCacheProtocol
    from trw_mcp.state._paths import TRWCallContext


def _detect_surface_phase() -> str:
    """Best-effort detection of the current ceremony phase.

    Returns the phase string (e.g. ``"IMPLEMENT"``) or ``""`` when detection fails.
    """
    try:
        from trw_mcp.state._paths import detect_current_phase

        phase = detect_current_phase()
        return phase.upper() if phase else ""
    except Exception:  # justified: fail-open, phase detection is optional
        return ""


def _load_recall_intel_cache(trw_dir: Path) -> _IntelCacheProtocol | None:
    """Return the local intelligence cache when it has bandit params."""
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.sync.cache import IntelligenceCache

        config = get_config()
        cache = IntelligenceCache(
            trw_dir,
            ttl_seconds=getattr(config, "intel_cache_ttl_seconds", 3600),
        )
        return cast("_IntelCacheProtocol", cache) if cache.get_bandit_params() is not None else None
    except Exception:  # justified: fail-open, intel cache wiring is optional
        return None


def build_recall_context(
    trw_dir: Path,
    query: str,
    call_ctx: TRWCallContext | None = None,
) -> RecallContext | None:
    """Build a RecallContext from the current session state.

    PRD-CORE-116-FR04: Populates inferred_domains as set[str] and threads
    client_profile/model_family from config.

    Best-effort: returns None if context can't be built.
    """
    from trw_mcp.scoring._recall import RecallContext, infer_domains

    current_phase: str | None = _detect_surface_phase() or None
    modified_files: list[str] = []

    try:
        import subprocess

        git_result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(trw_dir.parent) if trw_dir.name == ".trw" else str(trw_dir),
        )
        if git_result.returncode == 0:
            modified_files = [f.strip() for f in git_result.stdout.strip().split("\n") if f.strip()]
    except Exception:  # justified: fail-open, git probing is best-effort
        pass

    inferred_domains = infer_domains(file_paths=modified_files, query=query)

    # Thread client_profile and model_family from config (PRD-CORE-116)
    client_profile = ""
    model_family = ""
    try:
        from trw_mcp.models.config import get_config

        config = get_config()
        profile = config.client_profile
        client_profile = profile.client_id if profile else ""
        model_family = getattr(config, "model_family", "") or ""
    except Exception:  # justified: fail-open, config auto-detection is best-effort
        pass

    # Thread PRD knowledge IDs from artifact scanning (CORE-106/CORE-116)
    prd_knowledge_ids: set[str] = set()
    try:
        from trw_mcp.state._paths import find_active_run

        # PRD-CORE-141 FR03: thread ctx through so ctx-aware recall doesn't
        # scan-hijack another session's on-disk active run.
        active_run = find_active_run(context=call_ctx) if call_ctx is not None else find_active_run()
        if active_run:
            kr_path = Path(active_run) / "meta" / "knowledge_requirements.yaml"
            if kr_path.exists():
                reader = FileStateReader()
                kr_data = reader.read_yaml(kr_path)
                raw_ids = kr_data.get("learning_ids", [])
                if isinstance(raw_ids, list):
                    prd_knowledge_ids = {str(lid) for lid in raw_ids}
    except Exception:  # justified: fail-open, PRD knowledge ID loading is best-effort
        pass

    intel_cache = _load_recall_intel_cache(trw_dir)

    if not current_phase and not inferred_domains and not prd_knowledge_ids and intel_cache is None:
        return None

    logger.debug(
        "recall_context_built",
        phase=current_phase,
        domains=sorted(inferred_domains),
        client_profile=client_profile,
        model_family=model_family,
        prd_knowledge_ids_count=len(prd_knowledge_ids),
    )

    return RecallContext(
        current_phase=current_phase,
        inferred_domains=inferred_domains,
        modified_files=modified_files,
        client_profile=client_profile,
        model_family=model_family,
        prd_knowledge_ids=prd_knowledge_ids,
        intel_cache=intel_cache,
    )
