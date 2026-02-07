"""TRW self-learning tools — reflect, learn, learn_update, recall, script_save, claude_md_sync, learn_prune.

These 7 self-learning tools manage the .trw/ self-learning layer that makes
Claude Code progressively more effective in a specific repository over time.
When the optional ``claude-agent-sdk`` package is installed, several tools
gain LLM-augmented behavior (better summaries, relevance classification).
"""

from __future__ import annotations

import os
import re
import secrets
from datetime import date, datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.clients.llm import LLMClient
from trw_mcp.exceptions import ReflectionError, StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.learning import (
    Analytics,
    LearningEntry,
    LearningIndex,
    LearningStatus,
    Pattern,
    PatternIndex,
    Reflection,
    Script,
    ScriptIndex,
)
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateReader,
    FileStateWriter,
    model_to_dict,
)

logger = structlog.get_logger()

_config = TRWConfig()
_reader = FileStateReader()
_writer = FileStateWriter()
_events = FileEventLogger(_writer)
_llm = LLMClient(model=_config.llm_default_model)

# Named caps for list truncation (not user-tunable)
_MAX_ERROR_LEARNINGS = 5
_MAX_REPEATED_OPS = 3
_CLAUDEMD_LEARNING_CAP = 10
_CLAUDEMD_PATTERN_CAP = 5
_LLM_BATCH_CAP = 20
_LLM_EVENT_CAP = 30
_SLUG_MAX_LEN = 40

# CLAUDE.md TRW section markers (must stay consistent — parsing depends on these)
_TRW_AUTO_COMMENT = "<!-- TRW AUTO-GENERATED \u2014 do not edit between markers -->"
_TRW_MARKER_START = "<!-- trw:start -->"
_TRW_MARKER_END = "<!-- trw:end -->"

# Error event classification keywords
_ERROR_KEYWORDS = ("error", "fail", "exception", "crash", "timeout")


def _resolve_trw_dir() -> Path:
    """Resolve the .trw directory path from CWD or environment.

    Returns:
        Absolute path to the .trw directory.
    """
    env_root = os.environ.get("TRW_PROJECT_ROOT")
    root = Path(env_root).resolve() if env_root else Path.cwd().resolve()
    return root / _config.trw_dir


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
        trw_dir = _resolve_trw_dir()
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
        error_events = [e for e in events if _is_error_event(e)]
        phase_transitions = [
            e for e in events if e.get("event") == "phase_transition"
        ]
        repeated_ops = _find_repeated_operations(events)

        # Extract learnings
        new_learnings: list[dict[str, str]] = []
        llm_used = False

        # Try LLM-augmented analysis first
        if (events and _config.llm_enabled and _llm.available):  # pragma: no cover
            llm_learning = _llm_extract_learnings(events)
            if llm_learning is not None:
                llm_used = True
                for item in llm_learning:
                    learning_id = _generate_learning_id()
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
                    _save_learning_entry(trw_dir, entry)
                    new_learnings.append({
                        "id": learning_id,
                        "summary": entry.summary,
                    })

        # Fall back to mechanical extraction if LLM unavailable or failed
        if not llm_used:
            if error_events:
                for err in error_events[:_MAX_ERROR_LEARNINGS]:
                    learning_id = _generate_learning_id()
                    entry = LearningEntry(
                        id=learning_id,
                        summary=f"Error pattern: {err.get('event', 'unknown')}",
                        detail=str(err.get("data", err)),
                        tags=["error", "auto-discovered"],
                        evidence=[str(err.get("ts", ""))],
                        impact=0.6,
                    )
                    _save_learning_entry(trw_dir, entry)
                    new_learnings.append({
                        "id": learning_id,
                        "summary": entry.summary,
                    })

            if repeated_ops:
                for op_name, count in repeated_ops[:_MAX_REPEATED_OPS]:
                    learning_id = _generate_learning_id()
                    entry = LearningEntry(
                        id=learning_id,
                        summary=f"Repeated operation: {op_name} ({count}x)",
                        detail=f"Operation '{op_name}' was repeated {count} times — candidate for scripting",
                        tags=["repeated", "optimization"],
                        impact=0.5,
                        recurrence=count,
                    )
                    _save_learning_entry(trw_dir, entry)
                    new_learnings.append({
                        "id": learning_id,
                        "summary": entry.summary,
                    })

        # Create reflection log
        reflection_id = _generate_learning_id()
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

        # Update analytics
        _update_analytics(trw_dir, len(new_learnings))

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
    ) -> dict[str, str]:
        """Record a specific learning entry manually to .trw/learnings/.

        Args:
            summary: One-line summary of the learning.
            detail: Detailed description with context.
            tags: Categorization tags (e.g., ["testing", "gotcha"]).
            evidence: Supporting evidence (file paths, error messages, etc.).
            impact: Impact score from 0.0 to 1.0 (higher = more important).
        """
        trw_dir = _resolve_trw_dir()
        _writer.ensure_dir(trw_dir / _config.learnings_dir / _config.entries_dir)

        learning_id = _generate_learning_id()
        entry = LearningEntry(
            id=learning_id,
            summary=summary,
            detail=detail,
            tags=tags or [],
            evidence=evidence or [],
            impact=impact,
        )

        entry_path = _save_learning_entry(trw_dir, entry)

        # Update analytics counter
        _update_analytics(trw_dir, 1)

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
        trw_dir = _resolve_trw_dir()
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
        found = _find_entry_by_id(entries_dir, learning_id)
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
        _resync_learning_index(trw_dir)

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
    ) -> dict[str, object]:
        """Search learnings and patterns relevant to a query from .trw/.

        Args:
            query: Search query (keywords matched against summaries/details). Use "*" to list all.
            tags: Optional tag filter — only return entries matching these tags.
            min_impact: Minimum impact score filter (0.0-1.0).
            status: Optional status filter — 'active', 'resolved', or 'obsolete'.
        """
        trw_dir = _resolve_trw_dir()
        # Wildcard/empty query: skip token matching, return all (filtered by other params)
        is_wildcard = query.strip() in ("*", "")
        query_tokens = [] if is_wildcard else query.lower().split()

        # Search learnings
        matching_learnings: list[dict[str, object]] = []
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
                except (StateError, ValueError, TypeError):
                    continue

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

        # Read context files
        context: dict[str, object] = {}
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
            learnings_found=len(matching_learnings),
            patterns_found=len(matching_patterns),
        )

        return {
            "query": query,
            "learnings": matching_learnings,
            "patterns": matching_patterns,
            "context": context,
            "total_matches": len(matching_learnings) + len(matching_patterns),
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
        trw_dir = _resolve_trw_dir()
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
        trw_dir = _resolve_trw_dir()
        env_root = os.environ.get("TRW_PROJECT_ROOT")
        project_root = Path(env_root).resolve() if env_root else Path.cwd().resolve()

        # Collect high-impact learnings
        high_impact: list[dict[str, object]] = []
        entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir
        if entries_dir.exists():
            for entry_file in sorted(entries_dir.glob("*.yaml")):
                try:
                    data = _reader.read_yaml(entry_file)
                    impact = data.get("impact", 0.0)
                    entry_status = str(data.get("status", "active"))
                    if (isinstance(impact, (int, float))
                            and impact >= _config.learning_promotion_impact
                            and entry_status == "active"):
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
        template = _load_claude_md_template(trw_dir)

        if llm_used and llm_summary is not None:
            # LLM-generated summary replaces all sections
            context: dict[str, str] = {
                "architecture_section": "",
                "conventions_section": "",
                "categorized_learnings": llm_summary + "\n",
                "patterns_section": "",
                "adherence_section": "",
            }
        else:
            context = {
                "architecture_section": _render_architecture(arch_data),
                "conventions_section": _render_conventions(conv_data),
                "categorized_learnings": _render_categorized_learnings(high_impact),
                "patterns_section": _render_patterns(patterns),
                "adherence_section": _render_adherence(high_impact),
            }

        trw_section = _render_template(template, context)

        # Determine target path
        if scope == "sub" and target_dir:
            target = Path(target_dir).resolve() / "CLAUDE.md"
            max_lines = _config.sub_claude_md_max_lines
        else:
            target = project_root / "CLAUDE.md"
            max_lines = _config.claude_md_max_lines

        total_lines = _merge_trw_section(target, trw_section, max_lines)

        # Update analytics
        _update_analytics_sync(trw_dir)

        # Mark learnings as promoted
        for learning in high_impact:
            lid = learning.get("id", "")
            if isinstance(lid, str) and lid:
                _mark_promoted(trw_dir, lid)

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
        trw_dir = _resolve_trw_dir()
        entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir

        if not entries_dir.exists():
            return {"candidates": [], "actions": 0, "method": "none"}

        # Collect all entries (active, resolved, obsolete) for heuristic assessment
        all_entries: list[tuple[Path, dict[str, object]]] = []
        for entry_file in sorted(entries_dir.glob("*.yaml")):
            try:
                data = _reader.read_yaml(entry_file)
                all_entries.append((entry_file, data))
            except (StateError, ValueError, TypeError):
                continue

        if not all_entries:
            return {"candidates": [], "actions": 0, "method": "none"}

        candidates: list[dict[str, object]] = []

        if _config.llm_enabled and _llm.available:  # pragma: no cover
            # LLM-powered assessment
            candidates = _llm_assess_learnings(all_entries)
            method = "llm"
        else:
            # Multi-heuristic fallback (age, status, tags)
            candidates = _age_based_prune_candidates(all_entries)
            method = "heuristic"

        # Apply changes if not dry run
        actions = 0
        if not dry_run:
            for candidate in candidates:
                cid = str(candidate.get("id", ""))
                new_status = str(candidate.get("suggested_status", ""))
                if cid and new_status in ("resolved", "obsolete"):
                    _apply_status_update(trw_dir, cid, new_status)
                    actions += 1
            if actions > 0:
                _resync_learning_index(trw_dir)

        logger.info(
            "trw_learn_prune_complete",
            dry_run=dry_run,
            candidates=len(candidates),
            actions=actions,
            method=method,
        )

        return {
            "candidates": candidates,
            "actions": actions,
            "dry_run": dry_run,
            "method": method,
        }


# --- Private helpers ---


def _load_claude_md_template(trw_dir: Path) -> str:
    """Load CLAUDE.md template: .trw/templates/ > bundled > inline fallback.

    Resolution order:
    1. Project-local: ``trw_dir / templates_dir / "claude_md.md"``
    2. Bundled: ``data/templates/claude_md.md`` in package
    3. Inline fallback (minimal markers only)

    Args:
        trw_dir: Path to the .trw directory.

    Returns:
        Template string with ``{{placeholder}}`` tokens.
    """
    # 1. Project-local override
    project_template = trw_dir / _config.templates_dir / "claude_md.md"
    if project_template.exists():
        return project_template.read_text(encoding="utf-8")

    # 2. Bundled template
    data_dir = Path(__file__).parent.parent / "data" / "templates"
    bundled = data_dir / "claude_md.md"
    if bundled.exists():
        return bundled.read_text(encoding="utf-8")

    # 3. Inline fallback
    return (
        "\n"
        f"{_TRW_AUTO_COMMENT}\n"
        f"{_TRW_MARKER_START}\n"
        "\n"
        "## TRW Learnings (Auto-Generated)\n"
        "\n"
        "{{architecture_section}}"
        "{{conventions_section}}"
        "{{categorized_learnings}}"
        "{{patterns_section}}"
        "{{adherence_section}}"
        f"{_TRW_MARKER_END}\n"
    )


def _render_template(template: str, context: dict[str, str]) -> str:
    """Replace ``{{placeholder}}`` tokens and collapse empty sections.

    Args:
        template: Template string with ``{{key}}`` placeholders.
        context: Mapping of placeholder names to rendered content.

    Returns:
        Rendered markdown string with empty sections collapsed.
    """
    result = template
    for key, value in context.items():
        result = result.replace("{{" + key + "}}", value)
    # Collapse runs of 3+ consecutive blank lines to 2
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")
    return result


def _render_architecture(arch_data: dict[str, object]) -> str:
    """Render architecture context to markdown.

    Args:
        arch_data: Architecture data from context/architecture.yaml.

    Returns:
        Markdown string or empty string if no data.
    """
    if not arch_data:
        return ""
    lines: list[str] = ["### Architecture"]
    for key, val in arch_data.items():
        if val and key != "notes":
            lines.append(f"- {key}: {val}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _render_conventions(conv_data: dict[str, object]) -> str:
    """Render conventions context to markdown.

    Args:
        conv_data: Conventions data from context/conventions.yaml.

    Returns:
        Markdown string or empty string if no data.
    """
    if not conv_data:
        return ""
    lines: list[str] = ["### Conventions"]
    for key, val in conv_data.items():
        if val and key not in ("notes", "test_patterns"):
            lines.append(f"- {key}: {val}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _render_categorized_learnings(
    high_impact: list[dict[str, object]],
) -> str:
    """Render high-impact learnings categorized by tag type.

    Args:
        high_impact: List of high-impact learning entries.

    Returns:
        Markdown string with categorized learnings, or empty string.
    """
    if not high_impact:
        return ""
    categories: dict[str, list[str]] = {
        "Architecture": [],
        "Known Limitations": [],
        "Gotchas": [],
        "Key Learnings": [],
    }
    tag_to_category = {
        "architecture": "Architecture",
        "framework": "Architecture",
        "v17": "Architecture",
        "limitation": "Known Limitations",
        "improvement": "Known Limitations",
        "missing-tool": "Known Limitations",
        "gotcha": "Gotchas",
        "bug": "Gotchas",
        "configuration": "Gotchas",
    }
    for learning in high_impact[:_CLAUDEMD_LEARNING_CAP]:
        summary = str(learning.get("summary", ""))
        tags = learning.get("tags", [])
        tag_list = tags if isinstance(tags, list) else []
        placed = False
        for tag in tag_list:
            cat = tag_to_category.get(str(tag))
            if cat:
                categories[cat].append(summary)
                placed = True
                break
        if not placed:
            categories["Key Learnings"].append(summary)

    lines: list[str] = []
    for cat_name, entries in categories.items():
        if entries:
            lines.append(f"### {cat_name}")
            for entry in entries:
                lines.append(f"- {entry}")
            lines.append("")
    if lines:
        return "\n".join(lines) + "\n"
    return ""


def _render_patterns(patterns: list[dict[str, object]]) -> str:
    """Render discovered patterns to markdown.

    Args:
        patterns: List of pattern entries.

    Returns:
        Markdown string or empty string if no patterns.
    """
    if not patterns:
        return ""
    lines: list[str] = ["### Discovered Patterns"]
    for pattern in patterns[:_CLAUDEMD_PATTERN_CAP]:
        name = pattern.get("name", "")
        desc = pattern.get("description", "")
        lines.append(f"- **{name}**: {desc}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _render_adherence(high_impact: list[dict[str, object]]) -> str:
    """Render framework adherence directives from compliance learnings.

    Args:
        high_impact: List of high-impact learning entries.

    Returns:
        Markdown string with adherence directives, or empty string.
    """
    _adherence_tags = {"compliance", "process", "framework", "self-audit"}
    adherence_entries: list[str] = []
    for learning in high_impact:
        tags = learning.get("tags", [])
        tag_set = {str(t) for t in tags} if isinstance(tags, list) else set()
        if tag_set & _adherence_tags:
            detail = str(learning.get("detail", ""))
            for sentence in detail.split(". "):
                lower = sentence.lower()
                if any(kw in lower for kw in ("must", "should", "call ", "never", "always")):
                    clean = sentence.strip().rstrip(".")
                    if clean and len(clean) > 20:
                        adherence_entries.append(clean)

    if not adherence_entries:
        return ""
    lines: list[str] = ["### Framework Adherence"]
    seen: set[str] = set()
    count = 0
    for entry in adherence_entries:
        key = entry[:60].lower()
        if key not in seen and count < 8:
            lines.append(f"- {entry}")
            seen.add(key)
            count += 1
    lines.append("")
    return "\n".join(lines) + "\n"


def _merge_trw_section(target: Path, trw_section: str, max_lines: int) -> int:
    """Merge TRW auto-generated section into a CLAUDE.md file.

    Preserves user-written content outside the TRW markers.
    Replaces existing TRW section if markers are present,
    otherwise appends.

    Args:
        target: Path to the CLAUDE.md file.
        trw_section: The generated TRW section markdown.
        max_lines: Maximum allowed lines in the output file.

    Returns:
        Total line count of the written file.
    """
    if target.exists():
        existing = target.read_text(encoding="utf-8")
        if _TRW_MARKER_START in existing and _TRW_MARKER_END in existing:
            cut_start = existing.index(_TRW_MARKER_START)
            auto_idx = existing.rfind(_TRW_AUTO_COMMENT, 0, cut_start)
            if auto_idx >= 0:
                cut_start = auto_idx
            before = existing[:cut_start].rstrip()
            after_marker = existing.index(_TRW_MARKER_END) + len(_TRW_MARKER_END)
            after = existing[after_marker:].lstrip("\n")
            new_content = before + trw_section + "\n" + after
        else:
            new_content = existing.rstrip() + "\n" + trw_section + "\n"
    else:
        new_content = trw_section.lstrip() + "\n"

    content_lines = new_content.split("\n")
    if len(content_lines) > max_lines:
        content_lines = content_lines[:max_lines]
        content_lines.append("<!-- trw: truncated to line limit -->")
        new_content = "\n".join(content_lines)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new_content, encoding="utf-8")
    return len(new_content.split("\n"))


def _find_entry_by_id(
    entries_dir: Path,
    learning_id: str,
) -> tuple[Path, dict[str, object]] | None:
    """Find a learning entry file by scanning for a matching ID.

    Args:
        entries_dir: Path to the entries directory.
        learning_id: ID to search for.

    Returns:
        Tuple of (file_path, entry_data) if found, None otherwise.
    """
    for entry_file in entries_dir.glob("*.yaml"):
        try:
            data = _reader.read_yaml(entry_file)
            if data.get("id") == learning_id:
                return entry_file, data
        except (StateError, ValueError, TypeError):
            continue
    return None


def _generate_learning_id() -> str:
    """Generate a unique learning entry ID.

    Returns:
        String ID in format 'L-{random_hex}'.
    """
    return f"L-{secrets.token_hex(4)}"


def _is_error_event(event: dict[str, object]) -> bool:
    """Check if an event represents an error.

    Args:
        event: Event dictionary from events.jsonl.

    Returns:
        True if the event indicates an error or failure.
    """
    event_type = str(event.get("event", ""))
    return any(kw in event_type.lower() for kw in _ERROR_KEYWORDS)


def _find_repeated_operations(
    events: list[dict[str, object]],
) -> list[tuple[str, int]]:
    """Find operations that were repeated multiple times.

    Args:
        events: List of event dictionaries.

    Returns:
        List of (operation_name, count) tuples, sorted by count descending.
    """
    counts: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("event", ""))
        if event_type:
            counts[event_type] = counts.get(event_type, 0) + 1

    repeated = [
        (op, count) for op, count in counts.items()
        if count >= _config.learning_repeated_op_threshold
    ]
    repeated.sort(key=lambda x: x[1], reverse=True)
    return repeated


def _save_learning_entry(trw_dir: Path, entry: LearningEntry) -> Path:
    """Save a learning entry to .trw/learnings/entries/.

    Args:
        trw_dir: Path to .trw directory.
        entry: Learning entry to save.

    Returns:
        Path to the saved entry file.
    """
    raw = entry.summary[:_SLUG_MAX_LEN].lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    filename = f"{entry.created.isoformat()}-{slug}.yaml"
    entry_path = trw_dir / _config.learnings_dir / _config.entries_dir / filename
    _writer.write_yaml(entry_path, model_to_dict(entry))

    # Update index
    _update_learning_index(trw_dir, entry)

    return entry_path


def _update_learning_index(trw_dir: Path, entry: LearningEntry) -> None:
    """Update the learning index with a new entry.

    Args:
        trw_dir: Path to .trw directory.
        entry: New learning entry to add to index.
    """
    index_path = trw_dir / _config.learnings_dir / "index.yaml"
    index_data: dict[str, object] = {}
    if _reader.exists(index_path):
        index_data = _reader.read_yaml(index_path)

    entries_raw = index_data.get("entries", [])
    entries: list[dict[str, object]] = []
    if isinstance(entries_raw, list):
        entries = [e for e in entries_raw if isinstance(e, dict)]

    # Add new entry summary to index
    entries.append({
        "id": entry.id,
        "summary": entry.summary,
        "tags": entry.tags,
        "impact": entry.impact,
        "created": entry.created.isoformat(),
    })

    # Enforce max entries
    if len(entries) > _config.learning_max_entries:
        # Prune lowest impact entries
        entries.sort(key=lambda e: float(str(e.get("impact", 0.0))))
        entries = entries[-_config.learning_max_entries :]

    index_data["entries"] = entries
    index_data["total_count"] = len(entries)
    _writer.write_yaml(index_path, index_data)


def _resync_learning_index(trw_dir: Path) -> None:
    """Rebuild the learning index from all entry files on disk.

    Called after updates to ensure the index stays consistent.

    Args:
        trw_dir: Path to .trw directory.
    """
    entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir
    index_path = trw_dir / _config.learnings_dir / "index.yaml"

    entries: list[dict[str, object]] = []
    if entries_dir.exists():
        for entry_file in sorted(entries_dir.glob("*.yaml")):
            try:
                data = _reader.read_yaml(entry_file)
                entries.append({
                    "id": data.get("id", ""),
                    "summary": data.get("summary", ""),
                    "tags": data.get("tags", []),
                    "impact": data.get("impact", 0.5),
                    "status": data.get("status", "active"),
                    "created": str(data.get("created", "")),
                })
            except (StateError, ValueError, TypeError):
                continue

    index_data: dict[str, object] = {
        "entries": entries,
        "total_count": len(entries),
    }
    _writer.write_yaml(index_path, index_data)


def _update_analytics(trw_dir: Path, new_learnings_count: int) -> None:
    """Update .trw/context/analytics.yaml with reflection metrics.

    Args:
        trw_dir: Path to .trw directory.
        new_learnings_count: Number of new learnings produced.
    """
    context_dir = trw_dir / _config.context_dir
    _writer.ensure_dir(context_dir)
    analytics_path = context_dir / "analytics.yaml"

    data: dict[str, object] = {}
    if _reader.exists(analytics_path):
        data = _reader.read_yaml(analytics_path)

    sessions = int(str(data.get("sessions_tracked", 0))) + 1
    total_learnings = int(str(data.get("total_learnings", 0))) + new_learnings_count

    data["sessions_tracked"] = sessions
    data["total_learnings"] = total_learnings
    data["avg_learnings_per_session"] = round(total_learnings / max(sessions, 1), 2)

    _writer.write_yaml(analytics_path, data)


def _update_analytics_sync(trw_dir: Path) -> None:
    """Increment CLAUDE.md sync counter in analytics.

    Args:
        trw_dir: Path to .trw directory.
    """
    context_dir = trw_dir / _config.context_dir
    analytics_path = context_dir / "analytics.yaml"

    data: dict[str, object] = {}
    if _reader.exists(analytics_path):
        data = _reader.read_yaml(analytics_path)

    data["claude_md_syncs"] = int(str(data.get("claude_md_syncs", 0))) + 1
    _writer.write_yaml(analytics_path, data)


def _mark_promoted(trw_dir: Path, learning_id: str) -> None:
    """Mark a learning entry as promoted to CLAUDE.md.

    Args:
        trw_dir: Path to .trw directory.
        learning_id: ID of the learning entry to mark.
    """
    entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir
    if not entries_dir.exists():
        return

    found = _find_entry_by_id(entries_dir, learning_id)
    if found is not None:
        entry_file, data = found
        data["promoted_to_claude_md"] = True
        _writer.write_yaml(entry_file, data)


def _apply_status_update(trw_dir: Path, learning_id: str, new_status: str) -> None:
    """Apply a status update to a learning entry on disk.

    Args:
        trw_dir: Path to .trw directory.
        learning_id: ID of the learning entry to update.
        new_status: New status value to set.
    """
    entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir
    if not entries_dir.exists():
        return

    found = _find_entry_by_id(entries_dir, learning_id)
    if found is not None:
        entry_file, data = found
        data["status"] = new_status
        data["updated"] = date.today().isoformat()
        if new_status == LearningStatus.RESOLVED.value:
            data["resolved_at"] = date.today().isoformat()
        _writer.write_yaml(entry_file, data)


def _age_based_prune_candidates(
    entries: list[tuple[Path, dict[str, object]]],
) -> list[dict[str, object]]:
    """Identify stale learnings using multi-heuristic approach.

    Three heuristics (any match = candidate):
    1. Age-based: older than ``learning_prune_age_days`` with recurrence <= 1
    2. Status-based: entries already marked resolved/obsolete (cleanup stragglers)
    3. Tag-based: entries tagged ``bug`` or ``missing-tool`` with recurrence <= 1

    Args:
        entries: List of (file_path, entry_data) tuples.

    Returns:
        List of candidate dicts with id, summary, and suggested_status.
    """
    candidates: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    today = date.today()
    _bug_tags = {"bug", "missing-tool"}

    for _path, data in entries:
        entry_id = str(data.get("id", ""))
        if entry_id in seen_ids:
            continue

        created_str = str(data.get("created", ""))
        try:
            created = date.fromisoformat(created_str)
        except ValueError:
            continue

        age_days = (today - created).days
        recurrence = int(str(data.get("recurrence", 1)))
        entry_status = str(data.get("status", "active"))
        entry_tags = data.get("tags", [])
        tag_set = {str(t) for t in entry_tags} if isinstance(entry_tags, list) else set()

        # Heuristic 1: age-based (original)
        if age_days > _config.learning_prune_age_days and recurrence <= 1:
            candidates.append({
                "id": entry_id,
                "summary": data.get("summary", ""),
                "age_days": age_days,
                "suggested_status": "obsolete",
                "reason": f"Older than {_config.learning_prune_age_days} days ({age_days}d) with no recurrence increase",
            })
            seen_ids.add(entry_id)
            continue

        # Heuristic 2: status-based (resolved/obsolete stragglers still in active pool)
        if entry_status in ("resolved", "obsolete"):
            candidates.append({
                "id": entry_id,
                "summary": data.get("summary", ""),
                "age_days": age_days,
                "suggested_status": entry_status,
                "reason": f"Already marked {entry_status} — cleanup candidate",
            })
            seen_ids.add(entry_id)
            continue

        # Heuristic 3: bug/missing-tool tags with low recurrence
        if tag_set & _bug_tags and recurrence <= 1:
            candidates.append({
                "id": entry_id,
                "summary": data.get("summary", ""),
                "age_days": age_days,
                "suggested_status": "resolved",
                "reason": f"Tagged {tag_set & _bug_tags} with recurrence {recurrence} — likely fixed bug",
            })
            seen_ids.add(entry_id)

    return candidates


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
    for entry in learnings[:_CLAUDEMD_LEARNING_CAP]:
        items.append(f"- Learning: {entry.get('summary', '')} | Detail: {entry.get('detail', '')}")
    for pat in patterns[:_CLAUDEMD_PATTERN_CAP]:
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
