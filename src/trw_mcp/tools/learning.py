"""TRW self-learning tools — reflect, learn, learn_update, recall, script_save, claude_md_sync, learn_prune.

These 7 self-learning tools manage the .trw/ self-learning layer that makes
Claude Code progressively more effective in a specific repository over time.
When the optional ``claude-agent-sdk`` package is installed, several tools
gain LLM-augmented behavior (better summaries, relevance classification).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.clients.llm import LLMClient
from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.learning import (
    LearningEntry,
    LearningStatus,
    Reflection,
    Script,
)
from trw_mcp.scoring import rank_by_utility, utility_based_prune_candidates
from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir
from trw_mcp.state.analytics import (
    apply_status_update,
    find_entry_by_id,
    find_repeated_operations,
    generate_learning_id,
    is_error_event,
    mark_promoted,
    resync_learning_index,
    save_learning_entry,
    update_analytics,
    update_analytics_sync,
)
from trw_mcp.state.claude_md import (
    CLAUDEMD_LEARNING_CAP,
    CLAUDEMD_PATTERN_CAP,
    load_claude_md_template,
    merge_trw_section,
    render_adherence,
    render_architecture,
    render_behavioral_protocol,
    render_categorized_learnings,
    render_conventions,
    render_patterns,
    render_template,
)
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateReader,
    FileStateWriter,
    model_to_dict,
)
from trw_mcp.state.receipts import log_recall_receipt, prune_recall_receipts

logger = structlog.get_logger()

_config = TRWConfig()
_reader = FileStateReader()
_writer = FileStateWriter()
_events = FileEventLogger(_writer)
_llm = LLMClient(model=_config.llm_default_model)

# Named caps for list truncation (not user-tunable)
_MAX_ERROR_LEARNINGS = 5
_MAX_REPEATED_OPS = 3
_LLM_BATCH_CAP = 20
_LLM_EVENT_CAP = 30


def register_learning_tools(server: FastMCP) -> None:
    """Register all self-learning tools on the MCP server.

    Args:
        server: FastMCP server instance to register tools on.
    """

    @server.tool()
    def trw_reflect(
        run_path: str | None = None,
        scope: str = "session",
    ) -> dict[str, object]:
        """Analyze recent work events and extract structured learnings for .trw/.

        Args:
            run_path: Path to run directory for run-scoped reflection.
            scope: Reflection scope — "session", "run", or "wave".
        """
        trw_dir = resolve_trw_dir()
        _writer.ensure_dir(trw_dir / _config.learnings_dir / _config.entries_dir)
        _writer.ensure_dir(trw_dir / _config.reflections_dir)

        events: list[dict[str, object]] = []
        run_id: str | None = None

        # Collect events from run if path provided
        if run_path:
            resolved = Path(run_path).resolve()
            events_path = resolved / "meta" / "events.jsonl"
            if _reader.exists(events_path):
                events = _reader.read_jsonl(events_path)
            run_yaml = resolved / "meta" / "run.yaml"
            if _reader.exists(run_yaml):
                state = _reader.read_yaml(run_yaml)
                run_id_val = state.get("run_id")
                if isinstance(run_id_val, str):
                    run_id = run_id_val

        # Analyze events for patterns
        error_events = [e for e in events if is_error_event(e)]
        phase_transitions = [
            e for e in events if e.get("event") == "phase_transition"
        ]
        repeated_ops = find_repeated_operations(events)

        # Extract learnings
        new_learnings: list[dict[str, str]] = []
        llm_used = False

        # Try LLM-augmented analysis first
        if (events and _config.llm_enabled and _llm.available):  # pragma: no cover
            llm_learning = _llm_extract_learnings(events)
            if llm_learning is not None:
                llm_used = True
                for item in llm_learning:
                    learning_id = generate_learning_id()
                    raw_tags = item.get("tags", "")
                    parsed_tags: list[str] = (
                        raw_tags if isinstance(raw_tags, list)
                        else ["auto-discovered", "llm"]
                    )
                    entry = LearningEntry(
                        id=learning_id,
                        summary=str(item.get("summary", "LLM-extracted learning")),
                        detail=str(item.get("detail", "")),
                        tags=parsed_tags,
                        impact=float(str(item.get("impact", 0.6))),
                    )
                    save_learning_entry(trw_dir, entry)
                    new_learnings.append({
                        "id": learning_id,
                        "summary": entry.summary,
                    })

        # Fall back to mechanical extraction if LLM unavailable or failed
        if not llm_used:
            if error_events:
                for err in error_events[:_MAX_ERROR_LEARNINGS]:
                    learning_id = generate_learning_id()
                    entry = LearningEntry(
                        id=learning_id,
                        summary=f"Error pattern: {err.get('event', 'unknown')}",
                        detail=str(err.get("data", err)),
                        tags=["error", "auto-discovered"],
                        evidence=[str(err.get("ts", ""))],
                        impact=0.6,
                    )
                    save_learning_entry(trw_dir, entry)
                    new_learnings.append({
                        "id": learning_id,
                        "summary": entry.summary,
                    })

            if repeated_ops:
                for op_name, count in repeated_ops[:_MAX_REPEATED_OPS]:
                    learning_id = generate_learning_id()
                    entry = LearningEntry(
                        id=learning_id,
                        summary=f"Repeated operation: {op_name} ({count}x)",
                        detail=f"Operation '{op_name}' was repeated {count} times — candidate for scripting",
                        tags=["repeated", "optimization"],
                        impact=0.5,
                        recurrence=count,
                    )
                    save_learning_entry(trw_dir, entry)
                    new_learnings.append({
                        "id": learning_id,
                        "summary": entry.summary,
                    })

        # Create reflection log
        reflection_id = generate_learning_id()
        reflection = Reflection(
            id=reflection_id,
            run_id=run_id,
            scope=scope,
            timestamp=datetime.now(timezone.utc),
            events_analyzed=len(events),
            what_worked=[str(e.get("event")) for e in phase_transitions],
            what_failed=[str(e.get("event")) for e in error_events[:_MAX_ERROR_LEARNINGS]],
            repeated_patterns=[f"{op} ({c}x)" for op, c in repeated_ops[:_MAX_REPEATED_OPS]],
            new_learnings=[l["id"] for l in new_learnings],
        )

        reflection_path = (
            trw_dir
            / _config.reflections_dir
            / f"{date.today().isoformat()}-{reflection_id}.yaml"
        )
        _writer.write_yaml(reflection_path, model_to_dict(reflection))

        # Log reflection event to run's events.jsonl for phase gate tracking
        if run_path:
            resolved_run = Path(run_path).resolve()
            run_events_path = resolved_run / "meta" / "events.jsonl"
            if run_events_path.parent.exists():
                _events.log_event(run_events_path, "reflection_complete", {
                    "reflection_id": reflection_id,
                    "scope": scope,
                    "learnings_produced": len(new_learnings),
                })

        # Update analytics
        update_analytics(trw_dir, len(new_learnings))

        logger.info(
            "trw_reflect_complete",
            scope=scope,
            events_analyzed=len(events),
            learnings_produced=len(new_learnings),
        )

        return {
            "reflection_id": reflection_id,
            "scope": scope,
            "events_analyzed": len(events),
            "new_learnings": new_learnings,
            "error_patterns": len(error_events),
            "repeated_operations": len(repeated_ops),
            "llm_used": llm_used,
        }

    @server.tool()
    def trw_learn(
        summary: str,
        detail: str,
        tags: list[str] | None = None,
        evidence: list[str] | None = None,
        impact: float = 0.5,
        shard_id: str | None = None,
    ) -> dict[str, str]:
        """Record a specific learning entry manually to .trw/learnings/.

        Args:
            summary: One-line summary of the learning.
            detail: Detailed description with context.
            tags: Categorization tags (e.g., ["testing", "gotcha"]).
            evidence: Supporting evidence (file paths, error messages, etc.).
            impact: Impact score from 0.0 to 1.0 (higher = more important).
            shard_id: Optional shard identifier for sub-agent attribution.
        """
        trw_dir = resolve_trw_dir()
        _writer.ensure_dir(trw_dir / _config.learnings_dir / _config.entries_dir)

        learning_id = generate_learning_id()
        entry = LearningEntry(
            id=learning_id,
            summary=summary,
            detail=detail,
            tags=tags or [],
            evidence=evidence or [],
            impact=impact,
            shard_id=shard_id,
        )

        entry_path = save_learning_entry(trw_dir, entry)

        # Update analytics counter
        update_analytics(trw_dir, 1)

        logger.info(
            "trw_learn_recorded",
            learning_id=learning_id,
            summary=summary,
            impact=impact,
        )

        return {
            "learning_id": learning_id,
            "path": str(entry_path),
            "status": "recorded",
        }

    @server.tool()
    def trw_learn_update(
        learning_id: str,
        status: str | None = None,
        impact: float | None = None,
        summary: str | None = None,
        detail: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, str]:
        """Update an existing learning entry in .trw/learnings/.

        Args:
            learning_id: ID of the learning entry to update (e.g. 'L-abcd1234').
            status: New status — 'active', 'resolved', or 'obsolete'.
            impact: New impact score (0.0-1.0).
            summary: Updated one-line summary.
            detail: Updated detailed description.
            tags: Replacement tag list.
        """
        trw_dir = resolve_trw_dir()
        entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir

        if not entries_dir.exists():
            return {"learning_id": learning_id, "error": "No entries directory found"}

        # Validate status if provided
        valid_statuses = {s.value for s in LearningStatus}
        if status is not None and status not in valid_statuses:
            return {
                "learning_id": learning_id,
                "error": f"Invalid status: {status!r}. Valid: {sorted(valid_statuses)}",
            }

        # Find entry file by scanning for matching id
        found = find_entry_by_id(entries_dir, learning_id)
        if found is None:
            return {"learning_id": learning_id, "error": "Learning entry not found"}
        target_path, target_data = found

        # Apply updates
        if status is not None:
            target_data["status"] = status
            if status == LearningStatus.RESOLVED.value:
                target_data["resolved_at"] = date.today().isoformat()
        if impact is not None:
            target_data["impact"] = impact
        if summary is not None:
            target_data["summary"] = summary
        if detail is not None:
            target_data["detail"] = detail
        if tags is not None:
            target_data["tags"] = tags

        target_data["updated"] = date.today().isoformat()

        _writer.write_yaml(target_path, target_data)

        # Re-sync index
        resync_learning_index(trw_dir)

        logger.info(
            "trw_learn_updated",
            learning_id=learning_id,
            fields_changed=[
                k for k, v in [
                    ("status", status), ("impact", impact),
                    ("summary", summary), ("detail", detail),
                    ("tags", tags),
                ] if v is not None
            ],
        )

        return {
            "learning_id": learning_id,
            "path": str(target_path),
            "status": "updated",
        }

    @server.tool()
    def trw_recall(
        query: str,
        tags: list[str] | None = None,
        min_impact: float = 0.0,
        status: str | None = None,
        shard_id: str | None = None,
        max_results: int = _config.recall_max_results,
        compact: bool | None = None,
    ) -> dict[str, object]:
        """Search learnings and patterns relevant to a query from .trw/.

        Args:
            query: Search query (keywords matched against summaries/details).
                Use "*" to list all (auto-enables compact mode).
            tags: Optional tag filter — only return entries matching these tags.
            min_impact: Minimum impact score filter (0.0-1.0).
            status: Optional status filter — 'active', 'resolved', or 'obsolete'.
            shard_id: Optional shard identifier for receipt attribution.
            max_results: Maximum learnings to return (default 25, 0 = unlimited).
                Applied after filtering and ranking.
            compact: When True, return only id/summary/impact/tags/status per
                learning (omits detail, evidence, outcome_history, etc.).
                When None (default), auto-enables for wildcard queries.
        """
        trw_dir = resolve_trw_dir()
        # Wildcard/empty query: skip token matching, return all (filtered by other params)
        is_wildcard = query.strip() in ("*", "")
        query_tokens = [] if is_wildcard else query.lower().split()

        # PRD-FIX-013 FR03: auto-compact for wildcard when not explicitly set
        use_compact = compact if compact is not None else is_wildcard

        # Search learnings — track matched file paths for access updates
        matching_learnings: list[dict[str, object]] = []
        matched_files: list[Path] = []
        entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir
        if entries_dir.exists():
            for entry_file in sorted(entries_dir.glob("*.yaml")):
                try:
                    data = _reader.read_yaml(entry_file)
                    summary = str(data.get("summary", "")).lower()
                    detail = str(data.get("detail", "")).lower()
                    entry_tags = data.get("tags", [])
                    raw_impact = data.get("impact", 0.0)
                    entry_impact = float(str(raw_impact))

                    # Check impact threshold
                    if entry_impact < min_impact:
                        continue

                    # Check status filter
                    if status is not None:
                        entry_status = str(data.get("status", "active"))
                        if entry_status != status:
                            continue

                    # Check tag filter
                    if tags and isinstance(entry_tags, list):
                        if not any(t in entry_tags for t in tags):
                            continue

                    # Check query match — all tokens must appear in summary, detail, or tags
                    tag_text = " ".join(
                        str(t).lower() for t in entry_tags
                    ) if isinstance(entry_tags, list) else ""
                    text = summary + " " + detail + " " + tag_text
                    if all(token in text for token in query_tokens):
                        matching_learnings.append(data)
                        matched_files.append(entry_file)
                except (StateError, ValueError, TypeError):
                    continue

        # Update access tracking for ALL matched learnings (PRD-CORE-004 Phase 1a)
        # Note: tracking applies to all matches, not just returned results
        matched_ids: list[str] = []
        if matched_files:
            today_iso = date.today().isoformat()
            for entry_file in matched_files:
                try:
                    data = _reader.read_yaml(entry_file)
                    prev_count = int(str(data.get("access_count", 0)))
                    data["access_count"] = prev_count + 1
                    data["last_accessed_at"] = today_iso
                    _writer.write_yaml(entry_file, data)
                    entry_id = str(data.get("id", ""))
                    if entry_id:
                        matched_ids.append(entry_id)
                except (StateError, ValueError, TypeError):
                    continue

        # Log recall receipt
        log_recall_receipt(trw_dir, query, matched_ids, shard_id=shard_id)

        # Search patterns
        matching_patterns: list[dict[str, object]] = []
        patterns_dir = trw_dir / _config.patterns_dir
        if patterns_dir.exists():
            for pattern_file in sorted(patterns_dir.glob("*.yaml")):
                if pattern_file.name == "index.yaml":
                    continue
                try:
                    data = _reader.read_yaml(pattern_file)
                    name = str(data.get("name", "")).lower()
                    desc = str(data.get("description", "")).lower()
                    text = name + " " + desc
                    if all(token in text for token in query_tokens):
                        matching_patterns.append(data)
                except (StateError, ValueError, TypeError):
                    continue

        # Re-rank learnings by utility score (PRD-CORE-004 Phase 1b)
        ranked_learnings = rank_by_utility(
            matching_learnings, query_tokens, _config.recall_utility_lambda,
        )

        # PRD-FIX-013 FR04: total count before cap
        total_learnings_available = len(ranked_learnings)
        total_patterns_available = len(matching_patterns)

        # PRD-FIX-013 FR01: apply max_results cap
        if max_results > 0:
            ranked_learnings = ranked_learnings[:max_results]

        # PRD-FIX-013 FR02: compact mode — strip to essential fields
        if use_compact:
            compact_fields = _config.recall_compact_fields
            ranked_learnings = [
                {k: v for k, v in entry.items() if k in compact_fields}
                for entry in ranked_learnings
            ]

        # PRD-FIX-013 FR07: omit context for wildcard+compact
        context: dict[str, object] = {}
        if not (is_wildcard and use_compact):
            context_dir = trw_dir / _config.context_dir
            arch_path = context_dir / "architecture.yaml"
            conv_path = context_dir / "conventions.yaml"
            if _reader.exists(arch_path):
                context["architecture"] = _reader.read_yaml(arch_path)
            if _reader.exists(conv_path):
                context["conventions"] = _reader.read_yaml(conv_path)

        logger.info(
            "trw_recall_searched",
            query=query,
            learnings_found=len(ranked_learnings),
            patterns_found=len(matching_patterns),
            compact=use_compact,
        )

        return {
            "query": query,
            "learnings": ranked_learnings,
            "patterns": matching_patterns,
            "context": context,
            "total_matches": len(ranked_learnings) + len(matching_patterns),
            "total_available": total_learnings_available + total_patterns_available,
            "compact": use_compact,
            "max_results": max_results,
        }

    @server.tool()
    def trw_script_save(
        name: str,
        content: str,
        description: str,
        language: str = "bash",
    ) -> dict[str, str]:
        """Save a reusable script to .trw/scripts/ for cross-session reuse.

        Args:
            name: Script name (used as filename stem, alphanumeric + hyphens).
            content: Script content.
            description: What the script does.
            language: Script language — "bash", "python", etc.
        """
        trw_dir = resolve_trw_dir()
        scripts_dir = trw_dir / _config.scripts_dir
        _writer.ensure_dir(scripts_dir)

        # Determine extension
        ext_map: dict[str, str] = {
            "bash": ".sh",
            "python": ".py",
            "sh": ".sh",
            "py": ".py",
        }
        extension = ext_map.get(language, f".{language}")
        filename = f"{name}{extension}"
        script_path = scripts_dir / filename

        # Check if updating existing script
        is_update = script_path.exists()

        # Write script
        script_path.write_text(content, encoding="utf-8")

        # Update script index
        index_path = scripts_dir / "index.yaml"
        index_data: dict[str, object] = {}
        if _reader.exists(index_path):
            index_data = _reader.read_yaml(index_path)

        scripts_list: list[dict[str, object]] = []
        raw_scripts = index_data.get("scripts", [])
        if isinstance(raw_scripts, list):
            scripts_list = [s for s in raw_scripts if isinstance(s, dict)]

        # Update or add entry
        found = False
        for s in scripts_list:
            if s.get("name") == name:
                s["description"] = description
                s["last_refined"] = date.today().isoformat()
                usage = s.get("usage_count", 0)
                s["usage_count"] = (int(usage) if isinstance(usage, (int, float)) else 0) + 1
                found = True
                break

        if not found:
            script_entry = Script(
                name=name,
                description=description,
                filename=filename,
                language=language,
            )
            scripts_list.append(model_to_dict(script_entry))

        index_data["scripts"] = scripts_list
        _writer.write_yaml(index_path, index_data)

        action = "updated" if is_update else "created"
        logger.info(
            "trw_script_saved",
            name=name,
            action=action,
            path=str(script_path),
        )

        return {
            "name": name,
            "path": str(script_path),
            "status": action,
        }

    @server.tool()
    def trw_claude_md_sync(
        scope: str = "root",
        target_dir: str | None = None,
    ) -> dict[str, object]:
        """Generate/update CLAUDE.md from high-impact .trw/ learnings.

        Args:
            scope: Sync scope — "root" for project CLAUDE.md, "sub" for module-level.
            target_dir: Target directory for sub-CLAUDE.md generation.
        """
        trw_dir = resolve_trw_dir()
        project_root = resolve_project_root()

        # Collect high-impact learnings
        # For mature entries (q_observations >= threshold), use q_value
        # instead of static impact for promotion decision (PRD-CORE-004 1c)
        high_impact: list[dict[str, object]] = []
        entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir
        if entries_dir.exists():
            for entry_file in sorted(entries_dir.glob("*.yaml")):
                try:
                    data = _reader.read_yaml(entry_file)
                    entry_status = str(data.get("status", "active"))
                    if entry_status != "active":
                        continue

                    impact = data.get("impact", 0.0)
                    q_obs = int(str(data.get("q_observations", 0)))
                    q_val = data.get("q_value", impact)

                    # Use q_value for mature entries, impact for cold-start
                    if q_obs >= _config.q_cold_start_threshold:
                        score = float(str(q_val))
                    else:
                        score = float(str(impact)) if isinstance(impact, (int, float)) else 0.0

                    if score >= _config.learning_promotion_impact:
                        high_impact.append(data)
                except (StateError, ValueError, TypeError):
                    continue

        # Collect patterns
        patterns: list[dict[str, object]] = []
        patterns_dir = trw_dir / _config.patterns_dir
        if patterns_dir.exists():
            for pattern_file in sorted(patterns_dir.glob("*.yaml")):
                if pattern_file.name == "index.yaml":
                    continue
                try:
                    patterns.append(_reader.read_yaml(pattern_file))
                except (StateError, ValueError, TypeError):
                    continue

        # Collect context
        arch_data: dict[str, object] = {}
        conv_data: dict[str, object] = {}
        context_dir = trw_dir / _config.context_dir
        if _reader.exists(context_dir / "architecture.yaml"):
            arch_data = _reader.read_yaml(context_dir / "architecture.yaml")
        if _reader.exists(context_dir / "conventions.yaml"):
            conv_data = _reader.read_yaml(context_dir / "conventions.yaml")

        # Generate CLAUDE.md section
        llm_used = False
        llm_summary: str | None = None

        # Try LLM-powered summarization for better CLAUDE.md content
        if (high_impact or patterns) and _config.llm_enabled and _llm.available:  # pragma: no cover
            llm_summary = _llm_summarize_learnings(high_impact, patterns)
            if llm_summary is not None:
                llm_used = True

        # Load template and build context
        template = load_claude_md_template(trw_dir)

        behavioral_protocol = render_behavioral_protocol()

        if llm_used and llm_summary is not None:
            # LLM-generated summary replaces all sections
            tpl_context: dict[str, str] = {
                "behavioral_protocol": behavioral_protocol,
                "architecture_section": "",
                "conventions_section": "",
                "categorized_learnings": llm_summary + "\n",
                "patterns_section": "",
                "adherence_section": "",
            }
        else:
            tpl_context = {
                "behavioral_protocol": behavioral_protocol,
                "architecture_section": render_architecture(arch_data),
                "conventions_section": render_conventions(conv_data),
                "categorized_learnings": render_categorized_learnings(high_impact),
                "patterns_section": render_patterns(patterns),
                "adherence_section": render_adherence(high_impact),
            }

        trw_section = render_template(template, tpl_context)

        # Determine target path
        if scope == "sub" and target_dir:
            target = Path(target_dir).resolve() / "CLAUDE.md"
            max_lines = _config.sub_claude_md_max_lines
        else:
            target = project_root / "CLAUDE.md"
            max_lines = _config.claude_md_max_lines

        total_lines = merge_trw_section(target, trw_section, max_lines)

        # Update analytics
        update_analytics_sync(trw_dir)

        # Mark learnings as promoted
        for learning in high_impact:
            lid = learning.get("id", "")
            if isinstance(lid, str) and lid:
                mark_promoted(trw_dir, lid)

        logger.info(
            "trw_claude_md_synced",
            scope=scope,
            target=str(target),
            learnings_promoted=len(high_impact),
            patterns_included=len(patterns),
        )

        return {
            "path": str(target),
            "scope": scope,
            "learnings_promoted": len(high_impact),
            "patterns_included": len(patterns),
            "total_lines": total_lines,
            "status": "synced",
            "llm_used": llm_used,
        }

    @server.tool()
    def trw_learn_prune(
        dry_run: bool = True,
    ) -> dict[str, object]:
        """Review active learnings and mark resolved/obsolete ones.

        Args:
            dry_run: If True (default), report candidates without applying changes.
        """
        trw_dir = resolve_trw_dir()
        entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir

        # Prune recall receipts regardless of entry state
        receipts_pruned = 0
        if not dry_run:
            receipts_pruned = prune_recall_receipts(trw_dir)

        if not entries_dir.exists():
            return {
                "candidates": [], "actions": 0,
                "receipts_pruned": receipts_pruned, "method": "none",
            }

        # Collect all entries (active, resolved, obsolete) for heuristic assessment
        all_entries: list[tuple[Path, dict[str, object]]] = []
        for entry_file in sorted(entries_dir.glob("*.yaml")):
            try:
                data = _reader.read_yaml(entry_file)
                all_entries.append((entry_file, data))
            except (StateError, ValueError, TypeError):
                continue

        if not all_entries:
            return {
                "candidates": [], "actions": 0,
                "receipts_pruned": receipts_pruned, "method": "none",
            }

        candidates: list[dict[str, object]] = []

        if _config.llm_enabled and _llm.available:  # pragma: no cover
            # LLM-powered assessment
            candidates = _llm_assess_learnings(all_entries)
            method = "llm"
        else:
            # Utility-based pruning with Ebbinghaus decay (PRD-CORE-004)
            candidates = utility_based_prune_candidates(all_entries)
            method = "utility"

        # Apply changes if not dry run
        actions = 0
        if not dry_run:
            for candidate in candidates:
                cid = str(candidate.get("id", ""))
                new_status = str(candidate.get("suggested_status", ""))
                if cid and new_status in ("resolved", "obsolete"):
                    apply_status_update(trw_dir, cid, new_status)
                    actions += 1
            if actions > 0:
                resync_learning_index(trw_dir)

        logger.info(
            "trw_learn_prune_complete",
            dry_run=dry_run,
            candidates=len(candidates),
            actions=actions,
            receipts_pruned=receipts_pruned,
            method=method,
        )

        return {
            "candidates": candidates,
            "actions": actions,
            "receipts_pruned": receipts_pruned,
            "dry_run": dry_run,
            "method": method,
        }


# --- LLM helpers (require claude-agent-sdk) ---


def _llm_assess_learnings(  # pragma: no cover
    entries: list[tuple[Path, dict[str, object]]],
) -> list[dict[str, object]]:
    """Use LLM to assess whether active learnings are still relevant.

    Asks Haiku to classify each learning as ACTIVE, RESOLVED, or OBSOLETE.

    Args:
        entries: List of (file_path, entry_data) tuples.

    Returns:
        List of candidate dicts with id, summary, suggested_status, and reason.
    """
    import json as _json

    candidates: list[dict[str, object]] = []

    # Build batch prompt for efficiency
    summaries: list[str] = []
    for _path, data in entries[:_LLM_BATCH_CAP]:
        lid = str(data.get("id", ""))
        summary = str(data.get("summary", ""))
        detail = str(data.get("detail", ""))
        created = str(data.get("created", ""))
        summaries.append(f"- ID: {lid} | Created: {created} | Summary: {summary} | Detail: {detail}")

    if not summaries:
        return candidates

    prompt = (
        "Review these learning entries and assess whether each is still relevant.\n"
        "For each, respond with a JSON line: {\"id\": \"...\", \"status\": \"ACTIVE|RESOLVED|OBSOLETE\", \"reason\": \"...\"}\n"
        "Only include entries you recommend changing (not ACTIVE ones).\n\n"
        + "\n".join(summaries)
    )

    response = _llm.ask_sync(
        prompt,
        system="You are a learning lifecycle manager. Assess learning relevance concisely.",
    )

    if response is None:
        # LLM call failed — fall back to empty candidates
        return candidates

    # Parse response — each line should be a JSON object
    for line in response.strip().split("\n"):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            parsed = _json.loads(line)
            status_raw = str(parsed.get("status", "ACTIVE")).upper()
            if status_raw in ("RESOLVED", "OBSOLETE"):
                candidates.append({
                    "id": parsed.get("id", ""),
                    "summary": next(
                        (str(d.get("summary", ""))
                         for _, d in entries if d.get("id") == parsed.get("id")),
                        "",
                    ),
                    "suggested_status": status_raw.lower(),
                    "reason": parsed.get("reason", "LLM assessment"),
                })
        except (ValueError, KeyError):
            continue

    return candidates


def _llm_extract_learnings(  # pragma: no cover
    events: list[dict[str, object]],
) -> list[dict[str, object]] | None:
    """Use LLM to extract structured learnings from events.

    Returns None if LLM is unavailable or call fails, signaling
    the caller to fall back to mechanical extraction.

    Args:
        events: List of event dictionaries from events.jsonl.

    Returns:
        List of learning dicts with summary, detail, tags, impact, or None.
    """
    import json as _json

    # Build a condensed event summary for the prompt
    event_summaries: list[str] = []
    for evt in events[:_LLM_EVENT_CAP]:
        event_summaries.append(
            f"- {evt.get('event', 'unknown')}: {str(evt.get('data', ''))[:100]}"
        )

    if not event_summaries:
        return None

    prompt = (
        "Analyze these events from a software development session and extract key learnings.\n"
        "For each learning, respond with a JSON line:\n"
        '{\"summary\": \"one-line\", \"detail\": \"explanation\", \"tags\": [\"tag1\"], \"impact\": 0.5}\n'
        "Extract 1-5 learnings. Focus on actionable insights.\n\n"
        + "\n".join(event_summaries)
    )

    response = _llm.ask_sync(
        prompt,
        system="You are a software engineering learning extractor. Be concise and actionable.",
    )

    if response is None:
        return None

    learnings: list[dict[str, object]] = []
    for line in response.strip().split("\n"):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            parsed = _json.loads(line)
            if "summary" in parsed:
                learnings.append({
                    "summary": str(parsed["summary"]),
                    "detail": str(parsed.get("detail", "")),
                    "tags": parsed.get("tags", ["auto-discovered", "llm"]),
                    "impact": str(parsed.get("impact", "0.6")),
                })
        except (ValueError, KeyError):
            continue

    return learnings if learnings else None


def _llm_summarize_learnings(  # pragma: no cover
    learnings: list[dict[str, object]],
    patterns: list[dict[str, object]],
) -> str | None:
    """Use LLM to generate a concise categorized summary for CLAUDE.md.

    Returns None if LLM unavailable, signaling fallback to bullet-point listing.

    Args:
        learnings: High-impact active learning entries.
        patterns: Discovered patterns.

    Returns:
        Formatted markdown string for CLAUDE.md, or None.
    """
    if not learnings and not patterns:
        return None

    items: list[str] = []
    for entry in learnings[:CLAUDEMD_LEARNING_CAP]:
        items.append(f"- Learning: {entry.get('summary', '')} | Detail: {entry.get('detail', '')}")
    for pat in patterns[:CLAUDEMD_PATTERN_CAP]:
        items.append(f"- Pattern: {pat.get('name', '')} | {pat.get('description', '')}")

    prompt = (
        "Summarize these learnings and patterns into a concise CLAUDE.md section.\n"
        "Use 3-5 markdown H3 categories with actionable bullet points. Max 30 lines.\n"
        "Do NOT include markdown fences or top-level headers. Start directly with ### categories.\n\n"
        + "\n".join(items)
    )

    return _llm.ask_sync(
        prompt,
        system="You are a technical documentation writer. Be concise and organized.",
        model="sonnet",
    )
