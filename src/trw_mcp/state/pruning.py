"""Learning pruning — LLM-assisted and utility-based entry lifecycle management.

Extracted from tools/learning.py (Sprint 11) to separate pruning
logic from tool registration.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.clients.llm import LLMClient
from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.scoring import utility_based_prune_candidates
from trw_mcp.state.analytics import (
    apply_status_update,
    auto_prune_excess_entries,
    find_duplicate_learnings,
    resync_learning_index,
)
from trw_mcp.state.llm_helpers import llm_assess_learnings
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.state.receipts import prune_recall_receipts

_config = TRWConfig()
_reader = FileStateReader()

def _build_empty_result(receipts_pruned: int = 0) -> dict[str, object]:
    """Build an empty prune result structure.

    Args:
        receipts_pruned: Number of receipts pruned (default 0).

    Returns:
        Empty result dictionary with default values.
    """
    return {
        "candidates": [],
        "actions": 0,
        "receipts_pruned": receipts_pruned,
        "method": "none",
    }


def _load_entry_files(entries_dir: Path) -> list[tuple[Path, dict[str, object]]]:
    """Load all valid learning entry files from the entries directory.

    Args:
        entries_dir: Path to the learning entries directory.

    Returns:
        List of (file_path, entry_data) tuples for valid entries.
    """
    entries: list[tuple[Path, dict[str, object]]] = []
    for entry_file in sorted(entries_dir.glob("*.yaml")):
        try:
            data = _reader.read_yaml(entry_file)
            entries.append((entry_file, data))
        except (StateError, ValueError, TypeError):
            continue
    return entries


def _select_assessment_method(
    all_entries: list[tuple[Path, dict[str, object]]],
) -> tuple[list[dict[str, object]], str]:
    """Select and execute the appropriate learning assessment method.

    Args:
        all_entries: List of (file_path, entry_data) tuples.

    Returns:
        Tuple of (candidates, method_name) where candidates is the list of
        prune candidates and method_name is 'llm' or 'utility'.
    """
    llm = LLMClient(model=_config.llm_default_model)
    if _config.llm_enabled and llm.available:  # pragma: no cover
        return llm_assess_learnings(all_entries, llm), "llm"

    return utility_based_prune_candidates(all_entries), "utility"


def _apply_prune_candidates(
    trw_dir: Path,
    candidates: list[dict[str, object]],
) -> int:
    """Apply status updates for all valid prune candidates.

    Args:
        trw_dir: Path to .trw directory.
        candidates: List of candidate dictionaries with id and suggested_status.

    Returns:
        Number of status updates applied.
    """
    actions = 0
    for candidate in candidates:
        candidate_id = str(candidate.get("id", ""))
        suggested_status = str(candidate.get("suggested_status", ""))

        if not candidate_id:
            continue

        if suggested_status not in ("resolved", "obsolete"):
            continue

        apply_status_update(trw_dir, candidate_id, suggested_status)
        actions += 1

    return actions


def _apply_duplicate_updates(
    trw_dir: Path,
    duplicates: list[tuple[str, str, float]],
) -> int:
    """Apply status updates for duplicate learnings.

    Args:
        trw_dir: Path to .trw directory.
        duplicates: List of (older_id, newer_id, similarity) tuples.

    Returns:
        Number of status updates applied.
    """
    actions = 0
    for older_id, _newer_id, _similarity in duplicates:
        apply_status_update(trw_dir, older_id, "obsolete")
        actions += 1
    return actions


def _format_duplicates_for_output(
    duplicates: list[tuple[str, str, float]],
) -> list[dict[str, object]]:
    """Format duplicate tuples for output dictionary.

    Args:
        duplicates: List of (older_id, newer_id, similarity) tuples.

    Returns:
        List of dictionaries with older_id, newer_id, and similarity keys.
    """
    return [
        {"older_id": older_id, "newer_id": newer_id, "similarity": similarity}
        for older_id, newer_id, similarity in duplicates
    ]


def execute_prune(
    trw_dir: Path,
    *,
    dry_run: bool = True,
) -> dict[str, object]:
    """Execute the full prune pipeline: receipt cleanup, LLM/utility assessment, dedup.

    Args:
        trw_dir: Path to .trw directory.
        dry_run: If True, report candidates without applying changes.

    Returns:
        Dict with candidates, actions, receipts_pruned, method, duplicates, auto_prune.
    """
    entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir

    receipts_pruned = 0
    if not dry_run:
        receipts_pruned = prune_recall_receipts(trw_dir)

    if not entries_dir.exists():
        return _build_empty_result(receipts_pruned)

    all_entries = _load_entry_files(entries_dir)
    if not all_entries:
        return _build_empty_result(receipts_pruned)

    candidates, method = _select_assessment_method(all_entries)

    actions = 0
    if not dry_run:
        actions = _apply_prune_candidates(trw_dir, candidates)

    # PRD-QUAL-012-FR06: Jaccard dedup detection
    duplicates = find_duplicate_learnings(entries_dir, threshold=0.8)
    if not dry_run:
        duplicate_actions = _apply_duplicate_updates(trw_dir, duplicates)
        actions += duplicate_actions

    if not dry_run and actions > 0:
        resync_learning_index(trw_dir)

    # PRD-QUAL-012-FR06: Auto-pruning when active exceeds max_entries
    auto_prune_result = auto_prune_excess_entries(
        trw_dir,
        max_entries=_config.learning_max_entries,
        dry_run=dry_run,
    )

    return {
        "candidates": candidates,
        "actions": actions,
        "receipts_pruned": receipts_pruned,
        "dry_run": dry_run,
        "method": method,
        "duplicates": _format_duplicates_for_output(duplicates),
        "auto_prune": auto_prune_result,
    }
