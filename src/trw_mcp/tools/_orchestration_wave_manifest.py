"""Validated wave-manifest creation retained for ``trw_init`` compatibility."""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.exceptions import ValidationError
from trw_mcp.models.run import ShardCard, WaveEntry, WaveStatus
from trw_mcp.state.persistence import FileEventLogger, FileStateWriter, model_to_dict

logger = structlog.get_logger(__name__)
_writer = FileStateWriter()
_events = FileEventLogger(_writer)


def _validate_wave_plan(
    waves: list[dict[str, object]],
) -> tuple[list[WaveEntry], list[ShardCard], list[str]]:
    """Validate wave plan structure: unique IDs, valid deps, no cycles.

    Args:
        waves: List of wave definition dicts.

    Returns:
        Tuple of (wave_entries, shard_cards, errors).
    """
    errors: list[str] = []
    wave_entries: list[WaveEntry] = []
    shard_cards: list[ShardCard] = []
    all_wave_numbers: set[int] = set()
    all_shard_ids: set[str] = set()

    for wave_def in waves:
        wave_num = wave_def.get("wave")
        if not isinstance(wave_num, int) or wave_num < 1:
            errors.append(f"Invalid wave number: {wave_num} (must be positive integer)")
            continue

        if wave_num in all_wave_numbers:
            errors.append(f"Duplicate wave number: {wave_num}")
            continue
        all_wave_numbers.add(wave_num)

        raw_shards = wave_def.get("shards", [])
        if not isinstance(raw_shards, list):
            errors.append(f"Wave {wave_num}: shards must be a list")
            continue

        wave_shard_ids: list[str] = []
        for shard_def in raw_shards:
            if not isinstance(shard_def, dict):
                errors.append(f"Wave {wave_num}: shard definition must be a dict")
                continue

            shard_id = str(shard_def.get("id", ""))
            if not shard_id:
                errors.append(f"Wave {wave_num}: shard missing 'id' field")
                continue

            if shard_id in all_shard_ids:
                errors.append(f"Duplicate shard ID: {shard_id}")
                continue
            all_shard_ids.add(shard_id)

            # Ensure wave field matches parent wave
            shard_def_copy: dict[str, object] = dict(shard_def)
            shard_def_copy["wave"] = wave_num

            try:
                shard = ShardCard.model_validate(shard_def_copy)
                shard_cards.append(shard)
                wave_shard_ids.append(shard_id)
            except Exception as exc:
                errors.append(f"Wave {wave_num}, shard {shard_id}: {exc}")

        depends_on_raw = wave_def.get("depends_on", [])
        depends_on: list[int] = []
        if isinstance(depends_on_raw, list):
            for dep in depends_on_raw:
                if isinstance(dep, int):
                    depends_on.append(dep)
                else:
                    errors.append(f"Wave {wave_num}: depends_on must contain integers")

        wave_entry = WaveEntry(
            wave=wave_num,
            shards=wave_shard_ids,
            status=WaveStatus.PENDING,
            depends_on=depends_on,
        )
        wave_entries.append(wave_entry)

    # Check depends_on references valid wave numbers
    for entry in wave_entries:
        for dep in entry.depends_on:
            dep_int = dep if isinstance(dep, int) else int(str(dep))
            if dep_int not in all_wave_numbers:
                errors.append(f"Wave {entry.wave}: depends_on references non-existent wave {dep_int}")

    # Check for circular dependencies via DFS
    if not errors:
        graph: dict[int, list[int]] = {e.wave: [] for e in wave_entries}
        for entry in wave_entries:
            for dep in entry.depends_on:
                dep_int = dep if isinstance(dep, int) else int(str(dep))
                graph[entry.wave].append(dep_int)

        visited: set[int] = set()
        in_stack: set[int] = set()

        def _has_cycle(node: int) -> bool:
            visited.add(node)
            in_stack.add(node)
            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    if _has_cycle(neighbor):
                        return True
                elif neighbor in in_stack:
                    return True
            in_stack.discard(node)
            return False

        for wave_num in graph:
            if wave_num not in visited and _has_cycle(wave_num):
                errors.append("Circular dependency detected in wave depends_on graph")
                break

    return wave_entries, shard_cards, errors


def create_wave_plan(
    waves: list[dict[str, object]],
    run_path: Path,
) -> dict[str, object]:
    """Create wave plan — shared logic between trw_wave_plan and trw_init.

    Args:
        waves: List of wave definition dicts.
        run_path: Resolved run directory path.

    Returns:
        Wave plan summary dict.

    Raises:
        ValidationError: If wave plan validation fails.
    """
    wave_entries, shard_cards, errors = _validate_wave_plan(waves)
    if errors:
        raise ValidationError(
            f"Wave plan validation failed: {'; '.join(errors)}",
            error_count=len(errors),
        )

    # Write wave_manifest.yaml (canonical location: shards/)
    wave_manifest_data: dict[str, object] = {
        "waves": [model_to_dict(w) for w in wave_entries],
        "version": 1,
        "adaptation_history": [],
    }
    _writer.write_yaml(
        run_path / "shards" / "wave_manifest.yaml",
        wave_manifest_data,
    )

    # Write manifest.yaml (shard cards)
    shard_manifest_data: dict[str, object] = {
        "shards": [model_to_dict(s) for s in shard_cards],
    }
    _writer.write_yaml(
        run_path / "shards" / "manifest.yaml",
        shard_manifest_data,
    )

    # Pre-create scratch dirs
    for shard in shard_cards:
        _writer.ensure_dir(run_path / "scratch" / f"shard-{shard.id}")

    # Log event
    _events.log_event(
        run_path / "meta" / "events.jsonl",
        "wave_plan_created",
        {
            "wave_count": len(wave_entries),
            "shard_count": len(shard_cards),
        },
    )

    logger.info(
        "wave_plan_created",
        waves=len(wave_entries),
        shards=len(shard_cards),
    )

    return {
        "status": "wave_plan_created",
        "wave_count": len(wave_entries),
        "shard_count": len(shard_cards),
        "waves": [
            {
                "wave": e.wave,
                "shards": e.shards,
                "depends_on": e.depends_on,
            }
            for e in wave_entries
        ],
    }


__all__ = ["create_wave_plan"]
