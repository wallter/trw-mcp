"""TRW self-learning tools — reflect, learn, learn_update, recall, script_save, claude_md_sync, learn_prune.

These 7 self-learning tools manage the .trw/ self-learning layer that makes
Claude Code progressively more effective in a specific repository over time.
When the optional ``claude-agent-sdk`` package is installed, several tools
gain LLM-augmented behavior (better summaries, relevance classification).

Decomposed per PRD-FIX-010: tool stubs delegate to focused state modules.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.clients.llm import LLMClient
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
    extract_learnings_from_llm,
    extract_learnings_mechanical,
    find_entry_by_id,
    find_repeated_operations,
    find_success_patterns,
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
    collect_context_data,
    collect_patterns,
    collect_promotable_learnings,
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
from trw_mcp.state.llm_helpers import (
    llm_assess_learnings,
    llm_extract_learnings,
    llm_summarize_learnings,
)
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateReader,
    FileStateWriter,
    model_to_dict,
)
from trw_mcp.state.recall_search import (
    collect_context,
    search_entries,
    search_patterns,
    update_access_tracking,
)
from trw_mcp.state.receipts import log_recall_receipt, prune_recall_receipts

logger = structlog.get_logger()

_config = TRWConfig()
_reader = FileStateReader()
_writer = FileStateWriter()
_events = FileEventLogger(_writer)
_llm = LLMClient(model=_config.llm_default_model)

# Named caps for mechanical extraction
_MAX_ERROR_LEARNINGS = 5
_MAX_REPEATED_OPS = 3


def register_learning_tools(server: FastMCP) -> None:
    """Register all self-learning tools on the MCP server."""

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

        error_events = [e for e in events if is_error_event(e)]
        phase_transitions = [e for e in events if e.get("event") == "phase_transition"]
        repeated_ops = find_repeated_operations(events)
        success_patterns = find_success_patterns(events)

        # Extract learnings via LLM or mechanical fallback
        new_learnings: list[dict[str, str]] = []
        llm_used = False

        if events and _config.llm_enabled and _llm.available:  # pragma: no cover
            llm_result = llm_extract_learnings(events, _llm)
            if llm_result is not None:
                llm_used = True
                new_learnings = extract_learnings_from_llm(llm_result, trw_dir)

        if not llm_used:
            new_learnings = extract_learnings_mechanical(
                error_events, repeated_ops, trw_dir,
                max_errors=_MAX_ERROR_LEARNINGS,
                max_repeated=_MAX_REPEATED_OPS,
            )
            # Also extract success patterns as learnings (PRD-QUAL-001)
            for sp in success_patterns:
                sp_id = generate_learning_id()
                sp_entry = LearningEntry(
                    id=sp_id,
                    summary=sp["summary"],
                    detail=sp.get("detail", ""),
                    tags=["success", "pattern", "auto-discovered"],
                    impact=0.5,
                    recurrence=int(sp.get("count", 1)),
                )
                save_learning_entry(trw_dir, sp_entry)
                new_learnings.append({"id": sp_id, "summary": sp_entry.summary})

        # Create reflection log
        reflection_id = generate_learning_id()
        reflection = Reflection(
            id=reflection_id,
            run_id=run_id,
            scope=scope,
            timestamp=datetime.now(timezone.utc),
            events_analyzed=len(events),
            what_worked=(
                [str(e.get("event")) for e in phase_transitions]
                + [p["summary"] for p in success_patterns]
            ),
            what_failed=[str(e.get("event")) for e in error_events[:_MAX_ERROR_LEARNINGS]],
            repeated_patterns=[f"{op} ({c}x)" for op, c in repeated_ops[:_MAX_REPEATED_OPS]],
            new_learnings=[item["id"] for item in new_learnings],
        )

        reflection_path = (
            trw_dir / _config.reflections_dir
            / f"{date.today().isoformat()}-{reflection_id}.yaml"
        )
        _writer.write_yaml(reflection_path, model_to_dict(reflection))

        if run_path:
            resolved_run = Path(run_path).resolve()
            run_events_path = resolved_run / "meta" / "events.jsonl"
            if run_events_path.parent.exists():
                _events.log_event(run_events_path, "reflection_complete", {
                    "reflection_id": reflection_id,
                    "scope": scope,
                    "learnings_produced": len(new_learnings),
                })

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
            "success_patterns": len(success_patterns),
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
            id=learning_id, summary=summary, detail=detail,
            tags=tags or [], evidence=evidence or [],
            impact=impact, shard_id=shard_id,
        )
        entry_path = save_learning_entry(trw_dir, entry)
        update_analytics(trw_dir, 1)

        logger.info("trw_learn_recorded", learning_id=learning_id, summary=summary, impact=impact)
        return {"learning_id": learning_id, "path": str(entry_path), "status": "recorded"}

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

        valid_statuses = {s.value for s in LearningStatus}
        if status is not None and status not in valid_statuses:
            return {
                "learning_id": learning_id,
                "error": f"Invalid status: {status!r}. Valid: {sorted(valid_statuses)}",
            }

        found = find_entry_by_id(entries_dir, learning_id)
        if found is None:
            return {"learning_id": learning_id, "error": "Learning entry not found"}
        target_path, target_data = found

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
        resync_learning_index(trw_dir)

        logger.info(
            "trw_learn_updated",
            learning_id=learning_id,
            fields_changed=[
                k for k, v in [
                    ("status", status), ("impact", impact),
                    ("summary", summary), ("detail", detail), ("tags", tags),
                ] if v is not None
            ],
        )
        return {"learning_id": learning_id, "path": str(target_path), "status": "updated"}

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
            compact: When True, return only essential fields per learning.
                When None (default), auto-enables for wildcard queries.
        """
        trw_dir = resolve_trw_dir()
        is_wildcard = query.strip() in ("*", "")
        query_tokens = [] if is_wildcard else query.lower().split()
        use_compact = compact if compact is not None else is_wildcard

        entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir
        matching_learnings, matched_files = search_entries(
            entries_dir, query_tokens, _reader,
            tags=tags, min_impact=min_impact, status=status,
        )
        matched_ids = update_access_tracking(matched_files, _reader, _writer)
        log_recall_receipt(trw_dir, query, matched_ids, shard_id=shard_id)

        matching_patterns = search_patterns(
            trw_dir / _config.patterns_dir, query_tokens, _reader,
        )
        ranked_learnings = rank_by_utility(
            matching_learnings, query_tokens, _config.recall_utility_lambda,
        )

        total_learnings_available = len(ranked_learnings)
        total_patterns_available = len(matching_patterns)

        if max_results > 0:
            ranked_learnings = ranked_learnings[:max_results]

        if use_compact:
            compact_fields = _config.recall_compact_fields
            ranked_learnings = [
                {k: v for k, v in entry.items() if k in compact_fields}
                for entry in ranked_learnings
            ]

        context_data: dict[str, object] = {}
        if not (is_wildcard and use_compact):
            context_data = collect_context(trw_dir, _config.context_dir, _reader)

        logger.info(
            "trw_recall_searched", query=query,
            learnings_found=len(ranked_learnings),
            patterns_found=len(matching_patterns), compact=use_compact,
        )

        return {
            "query": query,
            "learnings": ranked_learnings,
            "patterns": matching_patterns,
            "context": context_data,
            "total_matches": len(ranked_learnings) + len(matching_patterns),
            "total_available": total_learnings_available + total_patterns_available,
            "compact": use_compact,
            "max_results": max_results,
        }

    @server.tool()
    def trw_script_save(
        name: str, content: str, description: str, language: str = "bash",
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

        ext_map: dict[str, str] = {"bash": ".sh", "python": ".py", "sh": ".sh", "py": ".py"}
        extension = ext_map.get(language, f".{language}")
        filename = f"{name}{extension}"
        script_path = scripts_dir / filename
        is_update = script_path.exists()

        _writer.write_text(script_path, content)

        index_path = scripts_dir / "index.yaml"
        index_data: dict[str, object] = {}
        if _reader.exists(index_path):
            index_data = _reader.read_yaml(index_path)

        scripts_list: list[dict[str, object]] = []
        raw_scripts = index_data.get("scripts", [])
        if isinstance(raw_scripts, list):
            scripts_list = [s for s in raw_scripts if isinstance(s, dict)]

        found_script = False
        for s in scripts_list:
            if s.get("name") == name:
                s["description"] = description
                s["last_refined"] = date.today().isoformat()
                usage = s.get("usage_count", 0)
                s["usage_count"] = (int(usage) if isinstance(usage, (int, float)) else 0) + 1
                found_script = True
                break

        if not found_script:
            script_entry = Script(
                name=name, description=description, filename=filename, language=language,
            )
            scripts_list.append(model_to_dict(script_entry))

        index_data["scripts"] = scripts_list
        _writer.write_yaml(index_path, index_data)

        action = "updated" if is_update else "created"
        logger.info("trw_script_saved", name=name, action=action, path=str(script_path))
        return {"name": name, "path": str(script_path), "status": action}

    @server.tool()
    def trw_claude_md_sync(
        scope: str = "root", target_dir: str | None = None,
    ) -> dict[str, object]:
        """Generate/update CLAUDE.md from high-impact .trw/ learnings.

        Args:
            scope: Sync scope — "root" for project CLAUDE.md, "sub" for module-level.
            target_dir: Target directory for sub-CLAUDE.md generation.
        """
        trw_dir = resolve_trw_dir()
        project_root = resolve_project_root()

        high_impact = collect_promotable_learnings(trw_dir, _config, _reader)
        patterns = collect_patterns(trw_dir, _config, _reader)
        arch_data, conv_data = collect_context_data(trw_dir, _config, _reader)

        llm_used = False
        llm_summary: str | None = None
        if (high_impact or patterns) and _config.llm_enabled and _llm.available:  # pragma: no cover
            llm_summary = llm_summarize_learnings(
                high_impact, patterns, _llm, CLAUDEMD_LEARNING_CAP, CLAUDEMD_PATTERN_CAP,
            )
            if llm_summary is not None:
                llm_used = True

        template = load_claude_md_template(trw_dir)
        behavioral_protocol = render_behavioral_protocol()

        if llm_used and llm_summary is not None:
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
        if scope == "sub" and target_dir:
            target = Path(target_dir).resolve() / "CLAUDE.md"
            max_lines = _config.sub_claude_md_max_lines
        else:
            target = project_root / "CLAUDE.md"
            max_lines = _config.claude_md_max_lines

        total_lines = merge_trw_section(target, trw_section, max_lines)
        update_analytics_sync(trw_dir)

        for learning in high_impact:
            lid = learning.get("id", "")
            if isinstance(lid, str) and lid:
                mark_promoted(trw_dir, lid)

        logger.info(
            "trw_claude_md_synced", scope=scope, target=str(target),
            learnings_promoted=len(high_impact), patterns_included=len(patterns),
        )
        return {
            "path": str(target), "scope": scope,
            "learnings_promoted": len(high_impact),
            "patterns_included": len(patterns),
            "total_lines": total_lines, "status": "synced", "llm_used": llm_used,
        }

    @server.tool()
    def trw_learn_prune(dry_run: bool = True) -> dict[str, object]:
        """Review active learnings and mark resolved/obsolete ones.

        Args:
            dry_run: If True (default), report candidates without applying changes.
        """
        trw_dir = resolve_trw_dir()
        entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir

        receipts_pruned = 0
        if not dry_run:
            receipts_pruned = prune_recall_receipts(trw_dir)

        if not entries_dir.exists():
            return {
                "candidates": [], "actions": 0,
                "receipts_pruned": receipts_pruned, "method": "none",
            }

        all_entries: list[tuple[Path, dict[str, object]]] = []
        for entry_file in sorted(entries_dir.glob("*.yaml")):
            try:
                data = _reader.read_yaml(entry_file)
                all_entries.append((entry_file, data))
            except (Exception,):
                continue

        if not all_entries:
            return {
                "candidates": [], "actions": 0,
                "receipts_pruned": receipts_pruned, "method": "none",
            }

        candidates: list[dict[str, object]] = []
        if _config.llm_enabled and _llm.available:  # pragma: no cover
            candidates = llm_assess_learnings(all_entries, _llm)
            method = "llm"
        else:
            candidates = utility_based_prune_candidates(all_entries)
            method = "utility"

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
            "trw_learn_prune_complete", dry_run=dry_run, candidates=len(candidates),
            actions=actions, receipts_pruned=receipts_pruned, method=method,
        )
        return {
            "candidates": candidates, "actions": actions,
            "receipts_pruned": receipts_pruned, "dry_run": dry_run, "method": method,
        }
