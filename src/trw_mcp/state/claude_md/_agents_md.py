"""AGENTS.md sync and per-client instruction file generation."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.analytics.entries import mark_promoted
from trw_mcp.state.claude_md._agents_md_size_gate import SizeGateMode
from trw_mcp.state.claude_md._agents_md_size_gate import (
    enforce_size_gate as _enforce_size_gate_impl,
)
from trw_mcp.state.claude_md._agents_md_size_gate import (
    resolve_instruction_size_gate_mode as _resolve_size_gate_mode_impl,
)
from trw_mcp.state.claude_md._instruction_clients import (
    _INSTRUCTION_SYNC_GENERATORS as _INSTRUCTION_SYNC_GENERATORS,
)
from trw_mcp.state.claude_md._instruction_clients import INSTRUCTION_SYNC_CLIENT_IDS, INSTRUCTION_SYNC_EXCLUSIONS
from trw_mcp.state.claude_md._instruction_clients import InstructionClientId as InstructionClientId
from trw_mcp.state.claude_md._instruction_clients import InstructionGeneratorResult as InstructionGeneratorResult
from trw_mcp.state.claude_md._instruction_clients import InstructionSyncGenerator as InstructionSyncGenerator
from trw_mcp.state.claude_md._instruction_clients import (
    _managed_manifest_hashes as _managed_manifest_hashes,
)
from trw_mcp.state.claude_md._instruction_clients import is_instruction_sync_client as _is_instruction_sync_client

# Surface-claim + orphan-cleanup helpers live in _orphan_strip (350-eLOC gate).
# Re-exported so `from ._agents_md import ...` keeps working for the carrier,
# bootstrap, and the test modules that import through this facade.
from trw_mcp.state.claude_md._orphan_strip import (
    _any_client_writes_agents_md as _any_client_writes_agents_md,
)
from trw_mcp.state.claude_md._orphan_strip import (
    _any_client_writes_claude_md as _any_client_writes_claude_md,
)
from trw_mcp.state.claude_md._orphan_strip import (
    _strip_trw_section as _strip_trw_section,
)
from trw_mcp.state.claude_md._orphan_strip import (
    strip_orphaned_claude_md_block as strip_orphaned_claude_md_block,
)
from trw_mcp.state.claude_md._parser import (
    TRW_AUTO_COMMENT,
    TRW_MARKER_END,
    TRW_MARKER_START,
    merge_trw_section,
    render_merged_content,
)
from trw_mcp.state.claude_md._review_md import _sanitize_summary
from trw_mcp.state.claude_md._review_md import recall_learnings as _default_recall

if TYPE_CHECKING:  # pragma: no cover - typing only
    from trw_mcp.state.claude_md._write_guard import InstructionWriteVerdict

logger = structlog.get_logger(__name__)

RecallFn = Callable[..., list[dict[str, object]]]

# The sync-client registry lives in ``_instruction_clients`` (see its docstring
# for why it is one readable unit). Re-exported here so importers keep this facade.
_INSTRUCTION_SYNC_CLIENT_IDS = INSTRUCTION_SYNC_CLIENT_IDS
_INSTRUCTION_SYNC_EXCLUSIONS = INSTRUCTION_SYNC_EXCLUSIONS


def detect_ide(target_dir: Path) -> list[str]:
    """Delegate IDE detection through a patch-friendly local wrapper."""
    from trw_mcp.bootstrap._utils import detect_ide as _detect_ide

    return _detect_ide(target_dir)


def _resolve_size_gate_mode(config: TRWConfig, project_root: Path) -> SizeGateMode:
    """Resolve the effective instruction-surface size-gate mode (PRD-QUAL-104 FR01)."""
    return _resolve_size_gate_mode_impl(project_root=project_root, configured_mode=config.instruction_size_gate_mode)


# Re-export the enforcer under the parent facade so callers keep one patch point.
_enforce_size_gate = _enforce_size_gate_impl


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


def _instruction_target_from_profile(client_id: InstructionClientId) -> InstructionFileTarget:
    """Build a typed instruction target from the client profile."""
    from trw_mcp.models.config._profiles import resolve_client_profile

    profile = resolve_client_profile(client_id)
    return InstructionFileTarget(
        client_id=client_id,
        instruction_path=profile.write_targets.instruction_path,
    )


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


def _recorded_or_detected_ides(project_root: Path) -> tuple[list[str], bool]:
    """Return this project's clients, and whether they came from the record.

    The flag is the point. A recorded, evidence-backed list is the project's own
    selection; a detected one is whatever is on the developer's PATH, since
    ``detect_ide`` reports cursor-ide from ``shutil.which("cursor")``. Callers
    that decide what to WRITE must know which they hold — returning the list
    alone let the caller apply a detection-only carve-out to a recorded list,
    which put the CLAUDE.md block back on the next sync for a project that had
    just had it removed.

    Thin indirection over the bootstrap helper so this module keeps one import
    point and tests can patch ``detect_ide`` on this facade as before.
    """
    try:
        from trw_mcp.bootstrap._template_claude_md import _recorded_targets

        recorded = _recorded_targets(project_root)
        if recorded:
            return recorded, True
    except Exception:  # justified: an unreadable record must fall back, not fail
        logger.debug("recorded_targets_unreadable", project_root=str(project_root), exc_info=True)
    return detect_ide(project_root), False


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
        # The RECORD first, detection only as fallback. Detection reports
        # claude-code for any project containing `.claude/`, which TRW creates
        # for EVERY client (hooks and skills are universal artifacts) — so
        # `trw_instructions_sync()` with its default client="auto", the call the
        # behavioral protocol tells agents to make at DELIVER, re-derived "this
        # is a Claude Code project" from our own scaffolding and reinjected the
        # CLAUDE.md block into codex and opencode projects.
        detected_ides, from_record = _recorded_or_detected_ides(project_root)
        instruction_targets = _instruction_targets_for_detected_ides(detected_ides) if root_scope else ()
        # PRD-CORE-240-FR04: the profile check is ANDed onto the old condition,
        # never substituted for it. Deriving purely from profiles looks cleaner
        # but WIDENS the write: `detect_ide` reports cursor-ide from
        # `shutil.which("cursor")` — a machine-global signal — so on any box with
        # Cursor installed, every project would gain an AGENTS.md it never asked
        # for. Requiring both keeps this strictly narrower than before, which is
        # all FR04 needs: a withdrawn client now fails the profile check even
        # though it still has an instruction target.
        return WriteTargetDecision(
            write_claude=_any_client_writes_claude_md(detected_ides, from_record=from_record),
            write_agents=(
                config.agents_md_enabled
                and root_scope
                and bool(instruction_targets)
                and _any_client_writes_agents_md(detected_ides)
            ),
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
            if not summary:
                continue
            bullet_lines.append(f"- {summary}")
            # PRD-CORE-165 FR-05: a surfaced (actually-injected) learning is
            # promoted. Skip entries without an id; never let promotion
            # bookkeeping abort the AGENTS.md injection.
            learning_id = str(entry.get("id", ""))
            if not learning_id:
                continue
            try:
                mark_promoted(trw_dir, learning_id)
            except Exception:  # justified: fail-open — promotion bookkeeping must not block injection
                logger.warning(
                    "agents_md_mark_promoted_failed",
                    learning_id=learning_id,
                    exc_info=True,
                )
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
    *,
    force: bool = False,
    dry_run: bool = False,
) -> tuple[bool, str | None, InstructionWriteVerdict | None]:
    """Generate and write AGENTS.md if needed.

    Returns ``(synced, path, verdict)``. The verdict carries the PRD-FIX-123
    guard outcome so the dispatcher can report a refusal or a dry-run diff
    instead of silently claiming success.
    """
    if not write_agents:
        return False, None, None

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

    exposed = resolve_exposed_tools(mode=config.tool_resolution_mode)

    if effective_client == "codex":
        agents_body = render_codex_trw_section(exposed_tools=exposed)
    elif config.effective_ceremony_mode == "light":
        agents_body = render_minimal_protocol()
    else:
        agents_body = render_agents_trw_section(exposed_tools=exposed)

    if config.agents_md_learning_injection:
        agents_body += _inject_learnings_to_agents(trw_dir, config, recall_fn=recall_fn)

    agents_section = f"{TRW_AUTO_COMMENT}\n{TRW_MARKER_START}\n\n{agents_body}\n{TRW_MARKER_END}\n"
    # PRD-FIX-123-FR07: measure the MERGED total, which is the quantity the
    # writer enforces ``max_auto_lines`` on. Measuring the rendered section alone
    # is why a 104-line section passed this gate and then truncated a 322-line
    # hand-written file. ``max`` preserves the render-side protection: a section
    # that alone exceeds the limit is still reported oversize.
    gate_lines = max(
        agents_section.count("\n"),
        len(render_merged_content(agents_target, agents_section).split("\n")),
    )

    # PRD-QUAL-104 FR01: promote the former warn-only oversize check to a
    # configurable gate. Look the gate helpers up via the parent module so
    # monkeypatches on ``_agents_md`` propagate (decomposition pattern).
    from trw_mcp.state.claude_md import _agents_md as _am

    gate_mode = _am._resolve_size_gate_mode(config, project_root)
    if _am._enforce_size_gate(str(agents_target), gate_lines, config.max_auto_lines, gate_mode) is not None:
        return False, None, None  # block mode: oversize already logged by the gate; abort the write.

    verdict = merge_trw_section(
        agents_target,
        agents_section,
        config.max_auto_lines,
        force=force,
        dry_run=dry_run,
        config=config,
        project_root=project_root,
    )
    return verdict.written, str(agents_target), verdict


def _sync_instruction_file_target(
    target: InstructionFileTarget,
    project_root: Path,
    *,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
) -> tuple[bool, str | None]:
    """Create or refresh the concrete instruction file for one target.

    *manifest_hashes* is the content-hash baseline describing TRW's last write.
    It must be captured before any TRW write in the current flow; see
    :func:`_managed_manifest_hashes` for why an on-disk read is not a valid
    substitute inside ``update-project``.
    """
    result = _INSTRUCTION_SYNC_GENERATORS[target.client_id](project_root, force, manifest_hashes)

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
    manifest_hashes: dict[str, str] | None = None,
) -> tuple[bool, str | None, list[str]]:
    """Sync all requested instruction files and return stable result metadata."""
    synced_paths: list[str] = []
    for target in instruction_targets:
        synced, path = _sync_instruction_file_target(target, project_root, manifest_hashes=manifest_hashes)
        if synced and path is not None:
            synced_paths.append(path)

    primary_path = synced_paths[0] if synced_paths else None
    return bool(synced_paths), primary_path, synced_paths


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
    # Line-anchored even though this path is currently unreachable: a substring
    # marker scan is the shape that destroyed 705 ROADMAP lines, and dead code
    # carrying it is a loaded gun for whoever rewires it.
    from trw_mcp.bootstrap._file_ops import find_marker_line_span

    _start_span = find_marker_line_span(content, TRW_MARKER_START, anchor="start")
    _end_span = find_marker_line_span(content, TRW_MARKER_END, anchor="end")
    start_idx = -1 if _start_span is None else _start_span[0]
    end_idx = -1 if _end_span is None else _end_span[0]
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

    # PRD-FIX-123-FR06: guarded like every other AGENTS.md writer. No production
    # caller today (PRD-CORE-240 would wire it) and the note above calls this path
    # "a loaded gun", so it is guarded NOW; the strip removes only generated bytes,
    # which the guard's block-delta attribution recognises, so it is never refused.
    from trw_mcp.bootstrap._file_ops import _new_result
    from trw_mcp.bootstrap._guarded_write import guarded_bootstrap_write

    strip_result = _new_result()
    if not guarded_bootstrap_write(
        agents_path,
        remaining_content,
        project_root=target_dir,
        markers=(TRW_MARKER_START, TRW_MARKER_END),
        result=strip_result,
        rel_path=agents_path.name,
        force=force,
    ):
        logger.warning("agents_md_trw_section_removal_failed", errors=strip_result["errors"])
        return False, ""

    primary_path = instruction_paths[0] if instruction_paths else ""
    return True, primary_path
