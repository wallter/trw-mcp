"""AGENTS.md sync and per-client instruction file generation."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias, TypeGuard

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md._parser import (
    TRW_AUTO_COMMENT,
    TRW_MARKER_END,
    TRW_MARKER_START,
    merge_trw_section,
)
from trw_mcp.state.claude_md._review_md import _sanitize_summary
from trw_mcp.state.claude_md._review_md import recall_learnings as _default_recall

logger = structlog.get_logger(__name__)

RecallFn = Callable[..., list[dict[str, object]]]
InstructionClientId: TypeAlias = Literal["opencode", "codex", "copilot", "gemini"]
InstructionGeneratorResult: TypeAlias = dict[str, list[str]]
InstructionSyncGenerator: TypeAlias = Callable[[Path, bool], InstructionGeneratorResult]

_INSTRUCTION_SYNC_CLIENT_IDS: tuple[InstructionClientId, ...] = ("opencode", "codex", "copilot", "gemini")


def detect_ide(target_dir: Path) -> list[str]:
    """Delegate IDE detection through a patch-friendly local wrapper."""
    from trw_mcp.bootstrap._utils import detect_ide as _detect_ide

    return _detect_ide(target_dir)


@dataclass(frozen=True, slots=True)
class InstructionFileTarget:
    """Concrete per-client instruction file target derived from profile metadata."""

    client_id: InstructionClientId
    instruction_path: str


@dataclass(frozen=True, slots=True)
class WriteTargetDecision:
    """Structured sync decision used by ``_sync.py``."""

    write_claude: bool
    write_agents: bool
    instruction_targets: tuple[InstructionFileTarget, ...]


def _is_instruction_sync_client(client_id: str) -> TypeGuard[InstructionClientId]:
    """Return whether the client has a real instruction-file generator."""
    return client_id in _INSTRUCTION_SYNC_CLIENT_IDS


def _instruction_target_from_profile(client_id: InstructionClientId) -> InstructionFileTarget:
    """Build a typed instruction target from the client profile."""
    from trw_mcp.models.config._profiles import resolve_client_profile

    profile = resolve_client_profile(client_id)
    return InstructionFileTarget(
        client_id=client_id,
        instruction_path=profile.write_targets.instruction_path,
    )


def _detect_opencode_model_family(project_root: Path) -> str:
    """Read ``opencode.json`` and return the detected OpenCode model family."""
    from trw_mcp.bootstrap._opencode import detect_model_family

    opencode_json_path = project_root / "opencode.json"
    if not opencode_json_path.exists():
        return "generic"

    try:
        import json

        opencode_data = json.loads(opencode_json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "generic"
    return detect_model_family(opencode_data)


def _generate_opencode_instruction_target(project_root: Path, force: bool = False) -> InstructionGeneratorResult:
    """Generate the OpenCode instruction file."""
    from trw_mcp.bootstrap._opencode import generate_opencode_instructions

    return generate_opencode_instructions(
        project_root,
        _detect_opencode_model_family(project_root),
        force=force,
    )


def _generate_codex_instruction_target(project_root: Path, force: bool = False) -> InstructionGeneratorResult:
    """Generate the Codex instruction file."""
    from trw_mcp.bootstrap._opencode import generate_codex_instructions

    return generate_codex_instructions(project_root, force=force)


def _generate_copilot_instruction_target(project_root: Path, force: bool = False) -> InstructionGeneratorResult:
    """Generate the Copilot instruction file."""
    from trw_mcp.bootstrap._copilot import generate_copilot_instructions

    return generate_copilot_instructions(project_root, force=force)


def _generate_gemini_instruction_target(project_root: Path, force: bool = False) -> InstructionGeneratorResult:
    """Generate the Gemini CLI instruction file (GEMINI.md)."""
    from trw_mcp.bootstrap._gemini import generate_gemini_instructions

    return generate_gemini_instructions(project_root, force=force)


_INSTRUCTION_SYNC_GENERATORS: dict[InstructionClientId, InstructionSyncGenerator] = {
    "opencode": _generate_opencode_instruction_target,
    "codex": _generate_codex_instruction_target,
    "copilot": _generate_copilot_instruction_target,
    "gemini": _generate_gemini_instruction_target,
}


def _instruction_targets_from_clients(client_ids: Iterable[str]) -> tuple[InstructionFileTarget, ...]:
    """Resolve profile-derived instruction targets for sync-capable clients."""
    targets: list[InstructionFileTarget] = []
    seen_clients: set[InstructionClientId] = set()
    for client_id in client_ids:
        if not _is_instruction_sync_client(client_id) or client_id in seen_clients:
            continue
        target = _instruction_target_from_profile(client_id)
        if target.instruction_path:
            targets.append(target)
            seen_clients.add(client_id)
    return tuple(targets)


def _instruction_targets_for_detected_ides(detected_ides: list[str]) -> tuple[InstructionFileTarget, ...]:
    """Map detected IDEs to typed instruction targets in detection order."""
    return _instruction_targets_from_clients(detected_ides)


def _determine_write_target_decision(
    client: str,
    config: TRWConfig,
    project_root: Path,
    scope: str,
) -> WriteTargetDecision:
    """Return the structured write decision for CLAUDE/AGENTS/instruction files."""
    from trw_mcp.models.config._profiles import resolve_client_profile

    root_scope = scope == "root"

    if client == "auto":
        detected_ides = detect_ide(project_root)
        instruction_targets = _instruction_targets_for_detected_ides(detected_ides) if root_scope else ()
        return WriteTargetDecision(
            write_claude="claude-code" in detected_ides or not detected_ides or (detected_ides == ["cursor-ide"]),
            write_agents=config.agents_md_enabled and root_scope and bool(instruction_targets),
            instruction_targets=instruction_targets,
        )

    if client == "all":
        instruction_targets = _instruction_targets_from_clients(_INSTRUCTION_SYNC_CLIENT_IDS) if root_scope else ()
        return WriteTargetDecision(
            write_claude=True,
            write_agents=config.agents_md_enabled and root_scope,
            instruction_targets=instruction_targets,
        )

    profile = resolve_client_profile(client)
    instruction_targets = _instruction_targets_from_clients((client,)) if root_scope else ()
    return WriteTargetDecision(
        write_claude=profile.write_targets.claude_md,
        write_agents=config.agents_md_enabled and root_scope and profile.write_targets.agents_md,
        instruction_targets=instruction_targets,
    )


def _determine_write_targets(
    client: str,
    config: TRWConfig,
    project_root: Path,
    scope: str,
) -> tuple[bool, bool, str | None]:
    """Determine whether to write CLAUDE.md and/or AGENTS.md."""
    from trw_mcp.models.config._profiles import resolve_client_profile

    decision = _determine_write_target_decision(client, config, project_root, scope)
    if decision.instruction_targets:
        instruction_path = decision.instruction_targets[0].instruction_path
    elif client not in ("auto", "all"):
        instruction_path = resolve_client_profile(client).write_targets.instruction_path
    else:
        instruction_path = None
    return decision.write_claude, decision.write_agents, instruction_path


def _inject_learnings_to_agents(
    trw_dir: Path,
    config: TRWConfig,
    recall_fn: RecallFn | None = None,
) -> str:
    """Build learning injection string for AGENTS.md or return empty string on error."""
    _recall = recall_fn if recall_fn is not None else _default_recall
    try:
        learning_entries = _recall(
            trw_dir,
            min_impact=config.agents_md_learning_min_impact,
            status="active",
            max_results=config.agents_md_learning_max,
        )
        bullet_lines: list[str] = []
        for entry in learning_entries:
            summary = _sanitize_summary(str(entry.get("summary", "")))
            if summary:
                bullet_lines.append(f"- {summary}")
        if bullet_lines:
            return "\n## Key Learnings\n\n" + "\n".join(bullet_lines) + "\n"
    except Exception:  # justified: fail-open — learning injection is optional AGENTS.md enrichment
        logger.warning("agents_md_learning_injection_failed", exc_info=True)
    return ""


def _sync_agents_md_if_needed(
    write_agents: bool,
    config: TRWConfig,
    project_root: Path,
    trw_dir: Path,
    client: str = "auto",
    recall_fn: RecallFn | None = None,
) -> tuple[bool, str | None]:
    """Generate and write AGENTS.md if needed."""
    if not write_agents:
        return False, None

    from trw_mcp.state.claude_md._static_sections import (
        render_agents_trw_section,
        render_codex_trw_section,
        render_minimal_protocol,
    )

    agents_target = project_root / "AGENTS.md"
    effective_client = client
    if client == "auto":
        detected_ides = detect_ide(project_root)
        if "codex" in detected_ides and "opencode" not in detected_ides:
            effective_client = "codex"

    # FR01 (PRD-CORE-135): resolve exposed tools so AGENTS.md only describes
    # tools the agent can actually call.
    from trw_mcp.state.claude_md._tool_manifest import resolve_exposed_tools

    exposed = resolve_exposed_tools(
        mode=config.effective_tool_exposure_mode,
        custom_list=config.tool_exposure_list,
    )

    if effective_client == "codex":
        agents_body = render_codex_trw_section(exposed_tools=exposed)
    elif config.effective_ceremony_mode == "light":
        agents_body = render_minimal_protocol()
    else:
        agents_body = render_agents_trw_section(exposed_tools=exposed)

    if config.agents_md_learning_injection:
        agents_body += _inject_learnings_to_agents(trw_dir, config, recall_fn=recall_fn)

    agents_section = f"{TRW_AUTO_COMMENT}\n{TRW_MARKER_START}\n\n{agents_body}\n{TRW_MARKER_END}\n"
    agents_lines = agents_section.count("\n")
    if agents_lines > config.max_auto_lines:
        logger.warning(
            "agents_md_section_oversized",
            lines=agents_lines,
            limit=config.max_auto_lines,
        )
    merge_trw_section(agents_target, agents_section, config.max_auto_lines)
    return True, str(agents_target)


def _sync_instruction_file_target(
    target: InstructionFileTarget,
    project_root: Path,
    *,
    force: bool = False,
) -> tuple[bool, str | None]:
    """Create or refresh the concrete instruction file for one target."""
    result = _INSTRUCTION_SYNC_GENERATORS[target.client_id](project_root, force)

    if result.get("errors"):
        logger.warning(
            "instruction_file_sync_failed",
            client=target.client_id,
            path=target.instruction_path,
            errors=result["errors"],
        )
        return False, None

    if any(result.get(key) for key in ("created", "updated", "preserved")):
        return True, target.instruction_path
    return False, None


def _resolve_instruction_target(
    instruction_path: str,
    client: str,
) -> InstructionFileTarget | None:
    """Resolve a legacy instruction-path request to a concrete sync target."""
    if _is_instruction_sync_client(client):
        return InstructionFileTarget(client_id=client, instruction_path=instruction_path)

    for supported_client in _INSTRUCTION_SYNC_CLIENT_IDS:
        profile_target = _instruction_target_from_profile(supported_client)
        if instruction_path == profile_target.instruction_path:
            return profile_target
    return None


def _sync_instruction_file_if_needed(
    instruction_path: str | None,
    project_root: Path,
    client: str,
    *,
    force: bool = False,
) -> tuple[bool, str | None]:
    """Backward-compatible single-target instruction sync helper."""
    if not instruction_path:
        return False, None

    target = _resolve_instruction_target(instruction_path, client)
    if target is None:
        return False, None
    return _sync_instruction_file_target(target, project_root, force=force)


def _sync_instruction_targets(
    project_root: Path,
    instruction_targets: tuple[InstructionFileTarget, ...],
) -> tuple[bool, str | None, list[str]]:
    """Sync all requested instruction files and return stable result metadata."""
    synced_paths: list[str] = []
    for target in instruction_targets:
        synced, path = _sync_instruction_file_target(target, project_root)
        if synced and path is not None:
            synced_paths.append(path)

    primary_path = synced_paths[0] if synced_paths else None
    return bool(synced_paths), primary_path, synced_paths


def _strip_trw_section(content: str) -> tuple[bool, str]:
    """Remove the TRW auto-generated block and its auto-comment from AGENTS.md."""
    start_idx = content.find(TRW_MARKER_START)
    end_idx = content.find(TRW_MARKER_END)
    if start_idx == -1 or end_idx == -1:
        return False, content

    remove_start = start_idx
    auto_comment_idx = content.rfind(TRW_AUTO_COMMENT, 0, start_idx)
    if auto_comment_idx != -1:
        between = content[auto_comment_idx + len(TRW_AUTO_COMMENT) : start_idx]
        if between.strip() == "":
            remove_start = auto_comment_idx

    remove_end = end_idx + len(TRW_MARKER_END)
    while remove_end < len(content) and content[remove_end] == "\n":
        remove_end += 1

    return True, content[:remove_start] + content[remove_end:]


def _migrate_trw_content_from_agents_md(
    target_dir: Path,
    config: TRWConfig,
    *,
    force: bool = False,
) -> tuple[bool, str]:
    """Migrate TRW auto-generated AGENTS.md content to per-client instruction files."""
    agents_path = target_dir / "AGENTS.md"

    if not agents_path.exists():
        return False, ""

    content = agents_path.read_text(encoding="utf-8")
    start_idx = content.find(TRW_MARKER_START)
    end_idx = content.find(TRW_MARKER_END)
    if start_idx == -1 or end_idx == -1:
        return False, ""

    detected_ides = detect_ide(target_dir)
    instruction_targets = _instruction_targets_for_detected_ides(detected_ides)
    instruction_paths: list[str] = []
    for target in instruction_targets:
        synced, synced_path = _sync_instruction_file_target(target, target_dir, force=force)
        if not synced or synced_path is None:
            logger.warning(
                "agents_md_instruction_migration_failed",
                client=target.client_id,
                path=target.instruction_path,
            )
            return False, ""
        instruction_paths.append(synced_path)

    stripped, remaining_content = _strip_trw_section(content)
    if not stripped:
        return False, ""

    try:
        agents_path.write_text(remaining_content, encoding="utf-8")
    except OSError as exc:
        logger.warning("agents_md_trw_section_removal_failed", error=str(exc))
        return False, ""

    primary_path = instruction_paths[0] if instruction_paths else ""
    return True, primary_path
