"""TRW wave management tools — plan, start, complete, context, prompt, adapt.

These 7 tools codify FRAMEWORK.md v18.0_TRW wave/shard lifecycle:
wave_plan → shard_start → shard_complete → wave_complete → wave_context → shard_prompt → wave_adapt

PRD-CORE-012: Wave Management Tooling
PRD-CORE-006: Dynamic Wave Adaptation
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.exceptions import ValidationError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import (
    ShardCard,
    ShardStatus,
    WaveEntry,
    WaveStatus,
)
from trw_mcp.scoring import process_outcome_for_event
from trw_mcp.state._paths import resolve_run_path
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateReader,
    FileStateWriter,
    lock_for_rmw,
    model_to_dict,
)

logger = structlog.get_logger()

_config = TRWConfig()
_reader = FileStateReader()
_writer = FileStateWriter()
_events = FileEventLogger(_writer)


def resolve_wave_manifest_path(run_path: Path) -> Path | None:
    """Find wave_manifest.yaml — prefer shards/ (canonical), fall back to meta/ (legacy).

    Args:
        run_path: Path to the run directory.

    Returns:
        Path to wave_manifest.yaml, or None if not found.
    """
    shards_path = run_path / "shards" / "wave_manifest.yaml"
    if shards_path.exists():
        return shards_path
    meta_path = run_path / "meta" / "wave_manifest.yaml"
    if meta_path.exists():
        return meta_path
    return None


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
            errors.append(
                f"Invalid wave number: {wave_num} (must be positive integer)"
            )
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
                    errors.append(
                        f"Wave {wave_num}: depends_on must contain integers"
                    )

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
                errors.append(
                    f"Wave {entry.wave}: depends_on references "
                    f"non-existent wave {dep_int}"
                )

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
            if wave_num not in visited:
                if _has_cycle(wave_num):
                    errors.append(
                        "Circular dependency detected in wave depends_on graph"
                    )
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


def register_wave_tools(server: FastMCP) -> None:
    """Register all 7 wave management tools on the MCP server.

    Args:
        server: FastMCP server instance to register tools on.
    """

    @server.tool()
    def trw_wave_plan(
        waves: list[dict[str, object]],
        run_path: str | None = None,
    ) -> dict[str, object]:
        """Create wave manifest with shard assignments and dependencies.

        Args:
            waves: List of wave definitions, each containing 'wave' (int),
                'shards' (list of shard card dicts), and optional 'depends_on' (list of ints).
            run_path: Path to run directory. Auto-detects if omitted.
        """
        resolved_path = resolve_run_path(run_path)
        return create_wave_plan(waves, resolved_path)

    @server.tool()
    def trw_shard_start(
        shard_id: str,
        run_path: str | None = None,
    ) -> dict[str, object]:
        """Mark shard as started, set up shard working directory.

        Args:
            shard_id: ID of the shard to start.
            run_path: Path to run directory. Auto-detects if omitted.
        """
        resolved_path = resolve_run_path(run_path)
        manifest_path = resolved_path / "shards" / "manifest.yaml"

        with lock_for_rmw(manifest_path):
            if not _reader.exists(manifest_path):
                raise ValidationError(
                    "No shard manifest found",
                    shard_id=shard_id,
                )

            data = _reader.read_yaml(manifest_path)
            raw_shards = data.get("shards", [])
            if not isinstance(raw_shards, list):
                raise ValidationError("Invalid shard manifest format")

            shard_idx: int | None = None
            shard_data: dict[str, object] | None = None
            for i, s in enumerate(raw_shards):
                if isinstance(s, dict) and s.get("id") == shard_id:
                    shard_idx = i
                    shard_data = dict(s)
                    break

            if shard_idx is None or shard_data is None:
                raise ValidationError(
                    f"Shard '{shard_id}' not found in manifest",
                    shard_id=shard_id,
                )

            current_status = str(shard_data.get("status", "pending"))
            if current_status != ShardStatus.PENDING.value:
                raise ValidationError(
                    f"Cannot start shard '{shard_id}': status is "
                    f"'{current_status}', expected 'pending'",
                    shard_id=shard_id,
                    current_status=current_status,
                )

            shard_data["status"] = ShardStatus.ACTIVE.value
            raw_shards[shard_idx] = shard_data
            data["shards"] = raw_shards
            _writer.write_yaml(manifest_path, data)

        # Ensure scratch dir exists
        _writer.ensure_dir(resolved_path / "scratch" / f"shard-{shard_id}")

        _events.log_event(
            resolved_path / "meta" / "events.jsonl",
            "shard_started",
            {
                "shard_id": shard_id,
                "wave": int(str(shard_data.get("wave", 0))),
                "title": str(shard_data.get("title", "")),
            },
        )

        return {
            "status": "shard_started",
            "shard_id": shard_id,
            "wave": shard_data.get("wave"),
            "title": str(shard_data.get("title", "")),
        }

    @server.tool()
    def trw_shard_complete(
        shard_id: str,
        status: str = "complete",
        output_path: str | None = None,
        run_path: str | None = None,
    ) -> dict[str, object]:
        """Mark shard as complete, validate output contracts.

        Args:
            shard_id: ID of the shard to complete.
            status: Completion status — 'complete', 'partial', or 'failed'.
            output_path: Optional path to shard output file.
            run_path: Path to run directory. Auto-detects if omitted.
        """
        valid_statuses = {
            ShardStatus.COMPLETE.value,
            ShardStatus.PARTIAL.value,
            ShardStatus.FAILED.value,
        }
        if status not in valid_statuses:
            raise ValidationError(
                f"Invalid completion status: '{status}'. "
                f"Must be one of: {', '.join(sorted(valid_statuses))}",
                shard_id=shard_id,
            )

        resolved_path = resolve_run_path(run_path)
        manifest_path = resolved_path / "shards" / "manifest.yaml"

        # Verify output_path exists if provided
        if output_path:
            abs_output = Path(output_path)
            if not abs_output.is_absolute():
                abs_output = resolved_path / output_path
            if not abs_output.exists():
                raise ValidationError(
                    f"Output path does not exist: {output_path}",
                    shard_id=shard_id,
                )

        shard_data: dict[str, object] = {}

        with lock_for_rmw(manifest_path):
            if not _reader.exists(manifest_path):
                raise ValidationError(
                    "No shard manifest found",
                    shard_id=shard_id,
                )

            data = _reader.read_yaml(manifest_path)
            raw_shards = data.get("shards", [])
            if not isinstance(raw_shards, list):
                raise ValidationError("Invalid shard manifest format")

            shard_idx: int | None = None
            for i, s in enumerate(raw_shards):
                if isinstance(s, dict) and s.get("id") == shard_id:
                    shard_idx = i
                    shard_data = dict(s)
                    break

            if shard_idx is None:
                raise ValidationError(
                    f"Shard '{shard_id}' not found in manifest",
                    shard_id=shard_id,
                )

            current_status = str(shard_data.get("status", ""))
            if current_status != ShardStatus.ACTIVE.value:
                raise ValidationError(
                    f"Cannot complete shard '{shard_id}': status is "
                    f"'{current_status}', expected 'active'",
                    shard_id=shard_id,
                    current_status=current_status,
                )

            shard_data["status"] = status
            if output_path:
                shard_data["output_path"] = output_path
            raw_shards[shard_idx] = shard_data
            data["shards"] = raw_shards
            _writer.write_yaml(manifest_path, data)

        wave_num = int(str(shard_data.get("wave", 0)))

        event_data: dict[str, object] = {
            "shard_id": shard_id,
            "wave": wave_num,
            "status": status,
        }
        if output_path:
            event_data["output_path"] = output_path
        _events.log_event(
            resolved_path / "meta" / "events.jsonl",
            "shard_completed",
            event_data,
        )

        # Outcome correlation — best-effort
        try:
            process_outcome_for_event("shard_complete")
        except Exception:  # noqa: BLE001
            pass

        return {
            "status": f"shard_{status}",
            "shard_id": shard_id,
            "wave": wave_num,
            "completion_status": status,
        }

    @server.tool()
    def trw_wave_complete(
        wave_number: int,
        run_path: str | None = None,
    ) -> dict[str, object]:
        """Finalize wave, run cross-shard validation.

        Args:
            wave_number: Wave number to complete (1-based).
            run_path: Path to run directory. Auto-detects if omitted.
        """
        resolved_path = resolve_run_path(run_path)
        wave_manifest_path = resolve_wave_manifest_path(resolved_path)

        if wave_manifest_path is None:
            raise ValidationError(
                "No wave manifest found",
                wave=wave_number,
            )

        shard_manifest_path = resolved_path / "shards" / "manifest.yaml"

        wave_data = _reader.read_yaml(wave_manifest_path)
        waves_raw = wave_data.get("waves", [])
        if not isinstance(waves_raw, list):
            raise ValidationError("Invalid wave manifest format")

        # Find target wave
        target_wave_idx: int | None = None
        target_wave_data: dict[str, object] | None = None
        for i, w in enumerate(waves_raw):
            if isinstance(w, dict) and w.get("wave") == wave_number:
                target_wave_idx = i
                target_wave_data = dict(w)
                break

        if target_wave_idx is None or target_wave_data is None:
            raise ValidationError(
                f"Wave {wave_number} not found in manifest",
                wave=wave_number,
            )

        # Read shard manifest
        if not _reader.exists(shard_manifest_path):
            raise ValidationError("No shard manifest found")

        shard_data = _reader.read_yaml(shard_manifest_path)
        raw_shards = shard_data.get("shards", [])
        if not isinstance(raw_shards, list):
            raise ValidationError("Invalid shard manifest format")

        # Get shards for this wave
        wave_shard_ids = target_wave_data.get("shards", [])
        if not isinstance(wave_shard_ids, list):
            wave_shard_ids = []

        wave_shards: list[dict[str, object]] = []
        for s in raw_shards:
            if isinstance(s, dict) and s.get("id") in wave_shard_ids:
                wave_shards.append(dict(s))

        # Count shard statuses
        counts: dict[str, int] = {
            "complete": 0, "partial": 0, "failed": 0,
            "pending": 0, "active": 0,
        }
        for s in wave_shards:
            st = str(s.get("status", "pending"))
            if st in counts:
                counts[st] += 1

        if counts["pending"] > 0 or counts["active"] > 0:
            raise ValidationError(
                f"Wave {wave_number} has {counts['pending']} pending and "
                f"{counts['active']} active shards — cannot complete",
                wave=wave_number,
            )

        # Determine wave status
        if counts["failed"] > 0:
            wave_status = WaveStatus.FAILED.value
        elif counts["partial"] > 0:
            wave_status = WaveStatus.PARTIAL.value
        else:
            wave_status = WaveStatus.COMPLETE.value

        # Validate output contracts for complete/partial shards
        from trw_mcp.state.validation import validate_wave_contracts

        wave_entry = WaveEntry.model_validate(target_wave_data)
        completable = [
            ShardCard.model_validate(s)
            for s in wave_shards
            if str(s.get("status")) in ("complete", "partial")
        ]

        validation_failures: list[dict[str, str]] = []
        if completable:
            try:
                failures = validate_wave_contracts(
                    wave_entry, completable, resolved_path,
                )
                validation_failures = [
                    {
                        "field": f.field,
                        "rule": f.rule,
                        "message": f.message,
                        "severity": f.severity,
                    }
                    for f in failures
                ]
            except ValidationError:
                pass  # No shards to validate is OK

        # Create checkpoint
        ts = datetime.now(timezone.utc).isoformat()
        checkpoint: dict[str, object] = {
            "ts": ts,
            "message": f"Wave {wave_number} completed: {wave_status}",
            "wave": wave_number,
            "wave_status": wave_status,
        }
        _writer.append_jsonl(
            resolved_path / "meta" / "checkpoints.jsonl",
            checkpoint,
        )

        # Update wave manifest status
        target_wave_data["status"] = wave_status
        waves_raw[target_wave_idx] = target_wave_data
        wave_data["waves"] = waves_raw
        _writer.write_yaml(wave_manifest_path, wave_data)

        # Log event
        _events.log_event(
            resolved_path / "meta" / "events.jsonl",
            "wave_completed",
            {
                "wave": wave_number,
                "status": wave_status,
                "shards_complete": counts["complete"],
                "shards_partial": counts["partial"],
                "shards_failed": counts["failed"],
                "validation_failures": len(validation_failures),
            },
        )

        # Outcome correlation — best-effort
        outcome = (
            "wave_validation_passed"
            if not validation_failures
            else "wave_validation_failed"
        )
        try:
            process_outcome_for_event(outcome)
        except Exception:  # noqa: BLE001
            pass

        return {
            "status": f"wave_{wave_status}",
            "wave": wave_number,
            "wave_status": wave_status,
            "shards_complete": counts["complete"],
            "shards_partial": counts["partial"],
            "shards_failed": counts["failed"],
            "validation_failures": validation_failures,
            "checkpoint_ts": ts,
        }

    @server.tool()
    def trw_wave_context(
        wave_number: int,
        run_path: str | None = None,
    ) -> dict[str, object]:
        """Return current wave state for orchestrator decisions.

        Args:
            wave_number: Wave number to get context for.
            run_path: Path to run directory. Auto-detects if omitted.
        """
        resolved_path = resolve_run_path(run_path)
        wave_manifest_path = resolve_wave_manifest_path(resolved_path)

        if wave_manifest_path is None:
            raise ValidationError(
                "No wave manifest found",
                wave=wave_number,
            )

        wave_data = _reader.read_yaml(wave_manifest_path)
        waves_raw = wave_data.get("waves", [])
        if not isinstance(waves_raw, list):
            raise ValidationError("Invalid wave manifest format")

        # Find target wave
        target_wave: dict[str, object] | None = None
        for w in waves_raw:
            if isinstance(w, dict) and w.get("wave") == wave_number:
                target_wave = dict(w)
                break

        if target_wave is None:
            raise ValidationError(
                f"Wave {wave_number} not found",
                wave=wave_number,
            )

        # Verify wave is complete or partial
        wave_status = str(target_wave.get("status", "pending"))
        if wave_status not in (WaveStatus.COMPLETE.value, WaveStatus.PARTIAL.value):
            raise ValidationError(
                f"Wave {wave_number} status is '{wave_status}' — "
                f"must be 'complete' or 'partial' for context generation",
                wave=wave_number,
            )

        wave_shard_ids = target_wave.get("shards", [])
        if not isinstance(wave_shard_ids, list):
            wave_shard_ids = []

        # Read shard manifest for metadata
        shard_manifest_path = resolved_path / "shards" / "manifest.yaml"
        shard_info: dict[str, dict[str, object]] = {}
        if _reader.exists(shard_manifest_path):
            shard_manifest = _reader.read_yaml(shard_manifest_path)
            raw_shards = shard_manifest.get("shards", [])
            if isinstance(raw_shards, list):
                for s in raw_shards:
                    if isinstance(s, dict):
                        sid = str(s.get("id", ""))
                        if sid in wave_shard_ids:
                            shard_info[sid] = dict(s)

        # Collect context from each shard
        shard_contexts: list[dict[str, object]] = []
        gaps: list[str] = []

        for shard_id_raw in wave_shard_ids:
            sid = str(shard_id_raw)
            info = shard_info.get(sid, {})
            shard_status = str(info.get("status", "unknown"))

            context_entry: dict[str, object] = {
                "id": sid,
                "status": shard_status,
                "summary": "",
                "key_findings": [],
            }

            # Try to read findings.yaml from shard scratch dir
            findings_path = (
                resolved_path / "scratch" / f"shard-{sid}" / "findings.yaml"
            )
            if _reader.exists(findings_path):
                try:
                    findings = _reader.read_yaml(findings_path)
                    context_entry["summary"] = str(findings.get("summary", ""))
                    raw_findings = findings.get("findings", [])
                    if isinstance(raw_findings, list):
                        context_entry["key_findings"] = [
                            str(f.get("key", "")) if isinstance(f, dict) else str(f)
                            for f in raw_findings[:5]
                        ]
                except Exception:
                    gaps.append(f"shard-{sid}/findings.yaml: unreadable")
            else:
                # Try output_contract file
                output_contract = info.get("output_contract")
                if isinstance(output_contract, dict):
                    contract_file = str(output_contract.get("file", ""))
                    if contract_file:
                        contract_path = resolved_path / contract_file
                        if _reader.exists(contract_path):
                            try:
                                contract_data = _reader.read_yaml(contract_path)
                                context_entry["summary"] = str(
                                    contract_data.get("summary", "")
                                )
                            except Exception:
                                gaps.append(f"{contract_file}: unreadable")
                        else:
                            gaps.append(f"shard-{sid}: output file missing")
                    else:
                        gaps.append(f"shard-{sid}: no output found")
                else:
                    gaps.append(f"shard-{sid}: no findings or output contract")

            shard_contexts.append(context_entry)

        context_doc: dict[str, object] = {
            "wave": wave_number,
            "status": wave_status,
            "shards": shard_contexts,
            "gaps": gaps,
        }

        # Write to blackboard
        blackboard_dir = resolved_path / "scratch" / "_blackboard"
        _writer.ensure_dir(blackboard_dir)
        _writer.write_yaml(
            blackboard_dir / f"wave-{wave_number}-context.yaml",
            context_doc,
        )

        _events.log_event(
            resolved_path / "meta" / "events.jsonl",
            "wave_context_generated",
            {
                "wave": wave_number,
                "shard_count": len(shard_contexts),
                "gaps": len(gaps),
            },
        )

        return context_doc

    @server.tool()
    def trw_shard_prompt(
        shard_id: str,
        instructions: str,
        run_path: str | None = None,
    ) -> dict[str, object]:
        """Generate shard-specific system prompt with context.

        Args:
            shard_id: ID of the shard to generate prompt for.
            instructions: User-provided instructions for the shard.
            run_path: Path to run directory. Auto-detects if omitted.
        """
        resolved_path = resolve_run_path(run_path)
        manifest_path = resolved_path / "shards" / "manifest.yaml"

        if not _reader.exists(manifest_path):
            raise ValidationError(
                "No shard manifest found",
                shard_id=shard_id,
            )

        data = _reader.read_yaml(manifest_path)
        raw_shards = data.get("shards", [])

        shard_data: dict[str, object] | None = None
        if isinstance(raw_shards, list):
            for s in raw_shards:
                if isinstance(s, dict) and s.get("id") == shard_id:
                    shard_data = dict(s)
                    break

        if shard_data is None:
            raise ValidationError(
                f"Shard '{shard_id}' not found",
                shard_id=shard_id,
            )

        # Read run state for MCP_MODE
        run_yaml_path = resolved_path / "meta" / "run.yaml"
        mcp_mode = "tool"
        run_id = ""
        if _reader.exists(run_yaml_path):
            state = _reader.read_yaml(run_yaml_path)
            mcp_mode = str(state.get("mcp_mode", "tool"))
            run_id = str(state.get("run_id", ""))

        shard_title = str(shard_data.get("title", shard_id))
        wave_num = shard_data.get("wave", 0)
        goals = shard_data.get("goals", [])
        goals_list: list[str] = goals if isinstance(goals, list) else []

        parts: list[str] = []

        # 1. Shard Identity Block
        parts.append("<shard_identity>")
        parts.append(f"Shard ID: {shard_id}")
        parts.append(f"Title: {shard_title}")
        parts.append(f"Wave: {wave_num}")
        parts.append(f"Run: {run_id}")
        if goals_list:
            parts.append("Goals:")
            for g in goals_list:
                parts.append(f"  - {g}")
        parts.append("</shard_identity>")
        parts.append("")

        # 2. Output Contract Block
        output_contract = shard_data.get("output_contract")
        if isinstance(output_contract, dict):
            parts.append("<output_contract>")
            parts.append("Expected output:")
            parts.append(
                f"  file: {output_contract.get('file', 'findings.yaml')}"
            )
            keys = output_contract.get(
                "keys", output_contract.get("schema_keys", [])
            )
            if isinstance(keys, list) and keys:
                parts.append(f"  required_keys: {keys}")
            optional = output_contract.get("optional_keys", [])
            if isinstance(optional, list) and optional:
                parts.append(f"  optional_keys: {optional}")
            parts.append("</output_contract>")
            parts.append("")

        # 3. MCP Guidance Block
        resolved_str = str(resolved_path)
        parts.append("<mcp_guidance>")
        parts.append(f"MCP_MODE: {mcp_mode}")
        parts.append(f"Run path: {resolved_str}")
        parts.append(
            f"Use trw_event(shard_id='{shard_id}') to log progress"
        )
        parts.append(
            f"Use trw_checkpoint(shard_id='{shard_id}') for state saves"
        )
        parts.append("</mcp_guidance>")
        parts.append("")

        # 4. Persistence Rules Block
        parts.append("<persistence_rules>")
        parts.append("- Write findings.yaml as LAST action before returning")
        parts.append("- Use status: partial on error or timeout")
        parts.append("- Use Write tool (not Bash heredoc) for file creation")
        parts.append(f"- Write outputs to scratch/shard-{shard_id}/")
        parts.append("</persistence_rules>")
        parts.append("")

        # 5. Input References Block
        input_refs = shard_data.get("input_refs", [])
        if isinstance(input_refs, list) and input_refs:
            parts.append("<input_references>")
            parts.append("Read these files for context:")
            for ref in input_refs:
                parts.append(f"  - {ref}")
            parts.append("</input_references>")
            parts.append("")

        # 6. User Instructions Block
        parts.append("<instructions>")
        parts.append(instructions)
        parts.append("</instructions>")

        prompt = "\n".join(parts)

        return {
            "shard_id": shard_id,
            "prompt": prompt,
            "token_estimate": len(prompt) // 4,
        }

    @server.tool()
    def trw_wave_adapt(
        wave_number: int,
        run_path: str | None = None,
        auto_approve: bool = True,
    ) -> dict[str, object]:
        """Evaluate completed wave outputs and propose/apply adaptations to remaining waves.

        7-step protocol: validate preconditions → collect shard outputs →
        evaluate triggers → generate proposal → gate → apply → log.

        Args:
            wave_number: Just-completed wave to evaluate.
            run_path: Path to run directory. Auto-detects if omitted.
            auto_approve: Auto-approve minor/moderate adaptations.
        """
        from trw_mcp.models.wave import (
            AdaptationAction,
            AdaptationProposal,
            AdaptationRecord,
            AdaptationSeverity,
            AdaptationTrigger,
            AdaptationTriggerType,
            ProposedChange,
        )

        # Step 1: Validate preconditions
        if not _config.adaptation_enabled:
            return {
                "status": "disabled",
                "message": "Wave adaptation is disabled in configuration",
            }

        resolved_path = resolve_run_path(run_path)
        wave_manifest_path = resolve_wave_manifest_path(resolved_path)

        if wave_manifest_path is None:
            raise ValidationError(
                "No wave manifest found",
                wave=wave_number,
            )

        manifest_data = _reader.read_yaml(wave_manifest_path)
        waves_raw = manifest_data.get("waves", [])
        if not isinstance(waves_raw, list):
            raise ValidationError("Invalid wave manifest format")

        # Check adaptation budget
        history = manifest_data.get("adaptation_history", [])
        if not isinstance(history, list):
            history = []
        current_version = int(str(manifest_data.get("version", 1)))

        if len(history) >= _config.max_adaptations_per_run:
            return {
                "status": "budget_exhausted",
                "message": f"Max adaptations ({_config.max_adaptations_per_run}) reached",
                "adaptations_used": len(history),
            }

        # Verify wave is complete
        target_wave: dict[str, object] | None = None
        for w in waves_raw:
            if isinstance(w, dict) and w.get("wave") == wave_number:
                target_wave = dict(w)
                break

        if target_wave is None:
            raise ValidationError(
                f"Wave {wave_number} not found",
                wave=wave_number,
            )

        wave_status = str(target_wave.get("status", "pending"))
        if wave_status not in ("complete", "partial", "failed"):
            raise ValidationError(
                f"Wave {wave_number} status is '{wave_status}' — "
                f"must be complete/partial/failed for adaptation",
                wave=wave_number,
            )

        # Step 2: Collect shard outputs and adaptation signals
        shard_manifest_path = resolved_path / "shards" / "manifest.yaml"
        shard_manifest_data: dict[str, object] = {}
        if _reader.exists(shard_manifest_path):
            shard_manifest_data = _reader.read_yaml(shard_manifest_path)

        raw_shards = shard_manifest_data.get("shards", [])
        if not isinstance(raw_shards, list):
            raw_shards = []

        wave_shard_ids = target_wave.get("shards", [])
        if not isinstance(wave_shard_ids, list):
            wave_shard_ids = []

        wave_shards: list[dict[str, object]] = []
        for s in raw_shards:
            if isinstance(s, dict) and s.get("id") in wave_shard_ids:
                wave_shards.append(dict(s))

        # Step 3: Evaluate triggers
        triggers: list[AdaptationTrigger] = []

        for shard in wave_shards:
            sid = str(shard.get("id", ""))

            # Trigger: low confidence shard
            confidence = str(shard.get("confidence", "medium"))
            if confidence == "low":
                triggers.append(AdaptationTrigger(
                    trigger_type=AdaptationTriggerType.LOW_CONFIDENCE,
                    source_shard=sid,
                    source_wave=wave_number,
                    description=f"Shard '{sid}' has low confidence",
                    severity=AdaptationSeverity.MODERATE,
                ))

            # Trigger: partial completion
            shard_status = str(shard.get("status", ""))
            if shard_status == "partial":
                triggers.append(AdaptationTrigger(
                    trigger_type=AdaptationTriggerType.PARTIAL_COMPLETION,
                    source_shard=sid,
                    source_wave=wave_number,
                    description=f"Shard '{sid}' only partially completed",
                    severity=AdaptationSeverity.MODERATE,
                ))

            # Trigger: failed shard
            if shard_status == "failed":
                triggers.append(AdaptationTrigger(
                    trigger_type=AdaptationTriggerType.VALIDATION_FAILURE,
                    source_shard=sid,
                    source_wave=wave_number,
                    description=f"Shard '{sid}' failed",
                    severity=AdaptationSeverity.MAJOR,
                ))

            # Trigger: shard signal file
            signal_path = (
                resolved_path / "scratch" / f"shard-{sid}"
                / "adaptation_signal.yaml"
            )
            if _reader.exists(signal_path):
                try:
                    signal = _reader.read_yaml(signal_path)
                    # Parse enum fields with fallback defaults for invalid values
                    trigger_type_str = str(signal.get("trigger_type", "shard_signal"))
                    trigger_type = (
                        AdaptationTriggerType(trigger_type_str)
                        if trigger_type_str in AdaptationTriggerType._value2member_map_
                        else AdaptationTriggerType.SHARD_SIGNAL
                    )
                    severity_str = str(signal.get("severity", "minor"))
                    signal_severity = (
                        AdaptationSeverity(severity_str)
                        if severity_str in AdaptationSeverity._value2member_map_
                        else AdaptationSeverity.MINOR
                    )
                    triggers.append(AdaptationTrigger(
                        trigger_type=trigger_type,
                        source_shard=sid,
                        source_wave=wave_number,
                        description=str(signal.get("description", "Shard signal")),
                        severity=signal_severity,
                        evidence=signal,
                    ))
                except Exception:
                    pass

        # Step 4: Generate proposal
        if not triggers:
            return {
                "status": "no_adaptation",
                "wave": wave_number,
                "message": "No adaptation triggers detected",
                "version": current_version,
            }

        # Determine overall severity (highest wins).
        # Note: AdaptationTrigger uses use_enum_values=True, so t.severity
        # is a plain str at runtime, not an enum instance.
        severity_strings: set[str] = {str(t.severity) for t in triggers}
        if AdaptationSeverity.MAJOR.value in severity_strings:
            overall_severity = AdaptationSeverity.MAJOR
        elif AdaptationSeverity.MODERATE.value in severity_strings:
            overall_severity = AdaptationSeverity.MODERATE
        else:
            overall_severity = AdaptationSeverity.MINOR

        # Build proposed changes
        changes: list[ProposedChange] = []
        shards_to_add = 0
        next_wave = wave_number + 1

        # Check if we'd exceed max total waves
        existing_wave_nums: set[int] = {
            int(str(w.get("wave", 0)))
            for w in waves_raw
            if isinstance(w, dict) and w.get("wave") is not None
        }

        for trigger in triggers:
            if trigger.trigger_type in (
                AdaptationTriggerType.PARTIAL_COMPLETION,
                AdaptationTriggerType.LOW_CONFIDENCE,
            ):
                if shards_to_add < _config.max_shards_added_per_adaptation:
                    changes.append(ProposedChange(
                        action=AdaptationAction.ADD_SHARD,
                        target_wave=next_wave,
                        target_shard=f"retry-{trigger.source_shard}",
                        description=f"Retry shard '{trigger.source_shard}' due to {trigger.trigger_type}",
                        shard_definition={
                            "id": f"retry-{trigger.source_shard}",
                            "title": f"Retry: {trigger.source_shard}",
                            "wave": next_wave,
                            "goals": [f"Complete work from {trigger.source_shard}"],
                            "input_refs": [f"scratch/shard-{trigger.source_shard}/"],
                        },
                    ))
                    shards_to_add += 1

            else:
                # Handle triggers that provide an add_shard signal in evidence
                evidence = trigger.evidence
                if not isinstance(evidence, dict) or not evidence.get("add_shard"):
                    continue
                if shards_to_add >= _config.max_shards_added_per_adaptation:
                    continue

                shard_def = evidence.get("add_shard", {})
                if not isinstance(shard_def, dict):
                    continue

                shard_id = str(
                    shard_def.get("id", f"signal-{trigger.source_shard}")
                )
                raw_goals = shard_def.get("goals", [])
                goals = list(raw_goals) if isinstance(raw_goals, list) else []

                changes.append(ProposedChange(
                    action=AdaptationAction.ADD_SHARD,
                    target_wave=next_wave,
                    target_shard=shard_id,
                    description=f"Add shard from signal by {trigger.source_shard}",
                    shard_definition={
                        "id": shard_id,
                        "title": str(shard_def.get("title", shard_id)),
                        "wave": next_wave,
                        "goals": goals,
                    },
                ))
                shards_to_add += 1

        proposal = AdaptationProposal(
            triggers=triggers,
            changes=changes,
            severity=overall_severity,
            rationale=f"Adaptation for wave {wave_number}: {len(triggers)} triggers detected",
            shards_added=shards_to_add,
        )

        # Step 5: Gate — approval check
        if overall_severity == AdaptationSeverity.MAJOR and auto_approve:
            return {
                "status": "approval_required",
                "wave": wave_number,
                "severity": overall_severity.value,
                "proposal": {
                    "triggers": [
                        {"type": t.trigger_type, "shard": t.source_shard, "description": t.description}
                        for t in triggers
                    ],
                    "changes": [
                        {"action": c.action, "shard": c.target_shard, "description": c.description}
                        for c in changes
                    ],
                    "shards_added": shards_to_add,
                },
                "message": "Major adaptation requires manual approval",
                "version": current_version,
            }

        if not changes:
            return {
                "status": "no_changes",
                "wave": wave_number,
                "triggers_detected": len(triggers),
                "message": "Triggers detected but no actionable changes generated",
                "version": current_version,
            }

        # Step 6: Apply — update manifest
        # Check max total waves
        need_new_wave = next_wave not in existing_wave_nums
        if need_new_wave and len(existing_wave_nums) >= _config.max_total_waves:
            return {
                "status": "max_waves_reached",
                "wave": wave_number,
                "message": f"Cannot add wave — max total waves ({_config.max_total_waves}) reached",
                "version": current_version,
            }

        with lock_for_rmw(wave_manifest_path):
            # Re-read under lock
            manifest_locked = _reader.read_yaml(wave_manifest_path)
            waves_locked = manifest_locked.get("waves", [])
            if not isinstance(waves_locked, list):
                waves_locked = []
            locked_version = int(str(manifest_locked.get("version", 1)))
            locked_history = manifest_locked.get("adaptation_history", [])
            if not isinstance(locked_history, list):
                locked_history = []

            # Add new wave if needed
            if need_new_wave:
                waves_locked.append({
                    "wave": next_wave,
                    "shards": [],
                    "status": "pending",
                    "depends_on": [wave_number],
                })

            # Add shards to target wave
            shard_manifest_locked = _reader.read_yaml(shard_manifest_path)
            shards_locked = shard_manifest_locked.get("shards", [])
            if not isinstance(shards_locked, list):
                shards_locked = []

            for change in changes:
                if change.action == AdaptationAction.ADD_SHARD:
                    # Add shard ID to wave entry
                    for w in waves_locked:
                        if isinstance(w, dict) and w.get("wave") == change.target_wave:
                            shard_list = w.get("shards", [])
                            if not isinstance(shard_list, list):
                                shard_list = []
                            shard_list.append(change.target_shard)
                            w["shards"] = shard_list
                            break

                    # Add shard card to shard manifest
                    shard_def = dict(change.shard_definition)
                    shard_def.setdefault("status", "pending")
                    shard_def.setdefault("confidence", "medium")
                    shards_locked.append(shard_def)

                    # Create scratch dir
                    _writer.ensure_dir(
                        resolved_path / "scratch" / f"shard-{change.target_shard}"
                    )

            # Increment version
            new_version = locked_version + 1

            # Append to history
            ts = datetime.now(timezone.utc).isoformat()
            record = AdaptationRecord(
                version=new_version,
                timestamp=ts,
                triggers=[t.trigger_type for t in triggers],
                severity=overall_severity,
                changes_summary=f"{shards_to_add} shards added",
                shards_added=shards_to_add,
                auto_approved=auto_approve,
            )
            locked_history.append(model_to_dict(record))

            # Write updated manifests
            manifest_locked["waves"] = waves_locked
            manifest_locked["version"] = new_version
            manifest_locked["adaptation_history"] = locked_history
            _writer.write_yaml(wave_manifest_path, manifest_locked)

            shard_manifest_locked["shards"] = shards_locked
            _writer.write_yaml(shard_manifest_path, shard_manifest_locked)

        # Step 7: Log event
        _events.log_event(
            resolved_path / "meta" / "events.jsonl",
            "wave_adapted",
            {
                "wave": wave_number,
                "version": new_version,
                "severity": overall_severity.value,
                "triggers": len(triggers),
                "shards_added": shards_to_add,
                "auto_approved": auto_approve,
            },
        )

        logger.info(
            "wave_adaptation_applied",
            wave=wave_number,
            version=new_version,
            severity=overall_severity.value,
            shards_added=shards_to_add,
        )

        return {
            "status": "adapted",
            "wave": wave_number,
            "version": new_version,
            "severity": overall_severity.value,
            "triggers_detected": len(triggers),
            "changes_applied": len(changes),
            "shards_added": shards_to_add,
            "auto_approved": auto_approve,
        }
