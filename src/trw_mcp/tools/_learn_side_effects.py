"""Learn-path side-effect helpers — extracted from _learn_impl.py for module-size compliance.

Belongs to the ``_learn_impl.py`` facade. Re-exported there for backward
compatibility with tests and callers that import via the parent module
(``from trw_mcp.tools._learn_impl import _content_policy_reject`` etc).
"""

from __future__ import annotations

import hashlib
import inspect
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import structlog

from trw_mcp.models.learning import (
    LearningConfidence,
    LearningProtectionTier,
    LearningType,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools._learning_helpers import LearningParams, _validate_source_type

logger = structlog.get_logger(__name__)

# Security audit 2026-04-18 H2: reject injection-shaped content at write time.
#
# PRD-IMPROVE-MCP-01 FR2: the original `rm\s+-rf\s+/` pattern false-positived on
# descriptive engineering prose that merely *mentions* a destructive command on
# a deeper path (e.g. "the fix cleans up rm -rf /tmp/<scratch>"). Narrowed so
# only genuinely catastrophic bare-root forms are blocked, while a rooted path
# with further components (a real, scoped path) passes:
#   blocked:  "rm -rf /"   "rm -rf /etc"   "rm -rf /*"   "rm -rf / "
#   allowed:  "rm -rf /tmp/foo"   "rm -rf /home/user/build"
# The trailing `(?![\w./-])` rejects a SECOND path separator after an optional
# single top-level component, so anything resolving below `/` is treated as
# descriptive prose rather than an injection payload.
_LEARN_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore (?:all )?previous instructions", re.IGNORECASE),
    re.compile(r"<script\b", re.IGNORECASE),
    re.compile(r"javascript\s*:", re.IGNORECASE),
    re.compile(r"rm\s+-rf\s+/(?:\*|\w+)?(?![\w./-])", re.IGNORECASE),
    re.compile(r"<instructions>", re.IGNORECASE),
    re.compile(r"<system>", re.IGNORECASE),
    re.compile(r"\[INST\]", re.IGNORECASE),
    re.compile(r"\[\[AI:", re.IGNORECASE),
    # Model-family chat-template control tokens (Llama/Mistral/Qwen/ChatML/
    # GPT). Learnings are recalled verbatim into future agent prompts via
    # trw_session_start, trw_recall, and the trw://learnings/summary resource,
    # so an embedded role-delimiter token is a direct prompt-injection payload.
    # These are control sequences that never appear in legitimate engineering
    # prose, so blocking them cannot false-positive on real notes.
    re.compile(r"<\|im_start\|>", re.IGNORECASE),
    re.compile(r"<\|im_end\|>", re.IGNORECASE),
    re.compile(r"<\|endoftext\|>", re.IGNORECASE),
    re.compile(r"<\|system\|>", re.IGNORECASE),
    re.compile(r"^###\s*system\s*:", re.IGNORECASE | re.MULTILINE),
    re.compile(r"<s>\s*\[INST\]", re.IGNORECASE),
    re.compile(r"system_prompt\s*:", re.IGNORECASE),
)

# Per-field caps: chosen to exceed p99 of real engineering notes while
# preventing wall-of-text prompt-injection payloads.
_MAX_SUMMARY_CHARS = 2000
_MAX_DETAIL_CHARS = 4000


def _store_accepts_positional_trw_dir(store_fn: Any) -> bool:
    """Return True if ``store_fn`` appears to accept a positional trw_dir."""
    try:
        signature = inspect.signature(store_fn)
    except (TypeError, ValueError):
        return True
    for parameter in signature.parameters.values():
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD):
            return True
    return False


def _append_provenance_signed(
    *,
    trw_dir: Path,
    learning_id: str,
    summary: str,
    detail: str,
    source_identity: str,
) -> None:
    """Append a signed provenance-chain record for a learning write.

    Fail-open by design: provenance augments auditability but must not make
    ``trw_learn`` fail when PyNaCl/key generation/file I/O is unavailable.
    """
    try:
        from trw_memory.security.keys import get_or_create_ed25519_key
        from trw_memory.security.provenance import ProvenanceEntry, append_signed

        content_hash = hashlib.sha256(f"{summary}{detail}".encode()).hexdigest()
        append_signed(
            trw_dir / "memory" / "security" / "provenance.jsonl",
            ProvenanceEntry(
                learning_id=learning_id,
                content_hash=content_hash,
                source_identity=source_identity or "agent",
            ),
            get_or_create_ed25519_key(trw_dir),
        )
    except Exception:  # justified: fail-open, provenance is advisory
        logger.debug("learn_provenance_append_failed", learning_id=learning_id, exc_info=True)


def _content_policy_reject(summary: str, detail: str) -> dict[str, object] | None:
    """Return a rejection payload if content exceeds caps or matches an
    injection pattern; None if the content is acceptable.

    Security audit 2026-04-18 H2.
    """
    if len(summary) > _MAX_SUMMARY_CHARS:
        return {
            "status": "rejected",
            "reason": "summary_too_long",
            "message": f"summary exceeds {_MAX_SUMMARY_CHARS} chars (got {len(summary)})",
        }
    if len(detail) > _MAX_DETAIL_CHARS:
        return {
            "status": "rejected",
            "reason": "detail_too_long",
            "message": f"detail exceeds {_MAX_DETAIL_CHARS} chars (got {len(detail)})",
        }
    combined = f"{summary}\n{detail}"
    for pattern in _LEARN_INJECTION_PATTERNS:
        if pattern.search(combined):
            return {
                "status": "rejected",
                "reason": "injection_pattern",
                "message": f"content matched blocked injection pattern {pattern.pattern!r}",
            }
    return None


def _handle_consolidation(
    learning_id: str,
    consolidated_from: list[str] | None,
    entries_dir: Path,
    reader: FileStateReader,
    writer: FileStateWriter,
    trw_dir: Path,
) -> None:
    """Handle auto-obsolete of superseded entries (PRD-FIX-052-FR04)."""
    if not consolidated_from:
        return

    from datetime import datetime, timezone

    from trw_mcp.state.analytics import find_entry_by_id
    from trw_mcp.state.memory_adapter import update_learning as adapter_update

    for ref_id in consolidated_from:
        try:
            update_result = adapter_update(
                trw_dir,
                learning_id=ref_id,
                status="obsolete",
            )
            if update_result.get("status") == "updated":
                try:
                    found = find_entry_by_id(entries_dir, ref_id)
                    if found is not None:
                        entry_path_ref, data_ref = found
                        data_ref["status"] = "obsolete"
                        _today = datetime.now(tz=timezone.utc).date().isoformat()
                        data_ref["resolved_at"] = _today
                        data_ref["updated"] = _today
                        writer.write_yaml(entry_path_ref, data_ref)
                except (OSError, ValueError, TypeError):
                    logger.debug(
                        "auto_obsolete_yaml_backup_failed",
                        ref_id=ref_id,
                        exc_info=True,
                    )
                logger.info(
                    "auto_obsolete_marked",
                    ref_id=ref_id,
                    compendium_id=learning_id,
                )
            else:
                logger.warning(
                    "auto_obsolete_not_found",
                    ref_id=ref_id,
                    compendium_id=learning_id,
                )
        except Exception:  # per-item error handling
            logger.warning(
                "auto_obsolete_failed",
                ref_id=ref_id,
                compendium_id=learning_id,
                exc_info=True,
            )


def _default_is_solution(summary: str) -> bool:
    """Fallback solution detection."""
    from trw_mcp.tools.learning import _is_solution_summary  # type: ignore[attr-defined]

    return bool(_is_solution_summary(summary))


def _save_yaml_backup(
    params: LearningParams,
    *,
    consolidated_from: list[str] | None,
    trw_dir: Path,
    entries_dir: Path,
    save_entry_fn: Callable[..., Path],
    update_analytics_fn: Callable[..., None],
) -> Path:
    """Save YAML backup via analytics (dual-write for rollback safety)."""
    try:
        import os as _os

        from trw_mcp.models.learning import LearningEntry
        from trw_mcp.scoring._io_boundary import _backfill_yaml_path_index

        # C5 FIX: Stamp source_run_id so the eval/scoring consumer can
        # distinguish self-authored entries from tar-pipe-injected entries in
        # chain evaluation runs. Prefer TRW_RUN_ID; fall back to TRW_CHAIN_ID.
        _source_run_id: str | None = _os.environ.get("TRW_RUN_ID") or _os.environ.get("TRW_CHAIN_ID") or None

        entry = LearningEntry(
            id=params.learning_id,
            summary=params.summary,
            detail=params.detail,
            tags=params.tags,
            evidence=params.evidence,
            impact=params.impact,
            shard_id=params.shard_id,
            source_type=_validate_source_type(params.source_type),
            source_identity=params.source_identity,
            client_profile=params.client_profile,
            model_id=params.model_id,
            assertions=cast("list[dict[str, object]]", params.assertions or []),
            consolidated_from=consolidated_from or [],
            type=LearningType(params.type) if isinstance(params.type, str) else params.type,
            nudge_line=params.nudge_line,
            expires=params.expires,
            confidence=LearningConfidence(params.confidence)
            if isinstance(params.confidence, str)
            else params.confidence,
            task_type=params.task_type,
            domain=params.domain or [],
            phase_origin=params.phase_origin,
            phase_affinity=params.phase_affinity or [],
            team_origin=params.team_origin,
            protection_tier=LearningProtectionTier(params.protection_tier)
            if isinstance(params.protection_tier, str)
            else params.protection_tier,
            anchors=params.anchors or [],
            anchor_validity=params.anchor_validity,
            source_run_id=_source_run_id,
        )
        entry_path: Path = Path(str(save_entry_fn(trw_dir, entry)))
        update_analytics_fn(trw_dir, 1)
        # Keep the scoring-side YAML lookup cache aware of freshly written
        # backups so outcome correlation preserves the dual-write contract.
        _backfill_yaml_path_index(params.learning_id, entry_path)
    except (OSError, ValueError, TypeError) as _save_exc:
        logger.warning(
            "learn_db_write_failed",
            summary=params.summary[:50],
            error=str(_save_exc),
        )
        entry_path = entries_dir / f"{params.learning_id}.yaml"

    return entry_path
