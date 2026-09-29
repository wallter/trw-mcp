"""AGENTS.md sync and per-client instruction file generation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.models.config import TRWConfig
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
from trw_mcp.state.claude_md._instruction_clients import INSTRUCTION_SYNC_CLIENT_IDS
from trw_mcp.state.claude_md._instruction_clients import InstructionClientId as InstructionClientId
from trw_mcp.state.claude_md._instruction_clients import InstructionGeneratorResult as InstructionGeneratorResult
from trw_mcp.state.claude_md._instruction_clients import InstructionSyncGenerator as InstructionSyncGenerator
from trw_mcp.state.claude_md._instruction_clients import (
    _managed_manifest_hashes as _managed_manifest_hashes,
)
from trw_mcp.state.claude_md._instruction_clients import is_instruction_sync_client as _is_instruction_sync_client
from trw_mcp.state.claude_md._instructions_link import agents_link_section, write_instructions_file

# Surface-claim + orphan-cleanup helpers live in _orphan_strip (350-eLOC gate).
# Re-exported so `from ._agents_md import ...` keeps working for the carrier,
# bootstrap, and the test modules that import through this facade.
from trw_mcp.state.claude_md._orphan_strip import _any_client_writes_agents_md as _any_client_writes_agents_md
from trw_mcp.state.claude_md._orphan_strip import _claude_code_claimed as _claude_code_claimed
from trw_mcp.state.claude_md._orphan_strip import _strip_trw_section as _strip_trw_section
from trw_mcp.state.claude_md._orphan_strip import retire_legacy_claude_md as retire_legacy_claude_md
from trw_mcp.state.claude_md._parser import merge_trw_section, render_merged_content

if TYPE_CHECKING:  # pragma: no cover - typing only
    from trw_mcp.state.claude_md._write_guard import InstructionWriteVerdict

logger = structlog.get_logger(__name__)

# The sync-client registry lives in ``_instruction_clients`` (see its docstring
# for why it is one readable unit). Re-exported here so importers keep this facade.
_INSTRUCTION_SYNC_CLIENT_IDS = INSTRUCTION_SYNC_CLIENT_IDS


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
    alone let the caller apply a detection-only carve-out to a recorded list.

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
    """Return the structured write decision for AGENTS.md and per-client instruction files."""
    from trw_mcp.models.config._profiles import resolve_client_profile

    root_scope = scope == "root"

    if client == "auto":
        # The RECORD first, detection only as fallback. Detection reports
        # claude-code for any project containing `.claude/`, which TRW creates
        # for EVERY client (hooks and skills are universal artifacts) — so
        # `trw-mcp instructions sync` with its default client="auto", the call the
        # behavioral protocol tells agents to make at DELIVER, re-derived "this
        # is a Claude Code project" from our own scaffolding.
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
        # claude-code has no per-client instruction target: AGENTS.md IS its
        # carrier, so its claim alone admits the AGENTS.md write.
        return WriteTargetDecision(
            write_agents=(
                config.agents_md_enabled
                and root_scope
                and (
                    _claude_code_claimed(detected_ides, from_record=from_record)
                    or (bool(instruction_targets) and _any_client_writes_agents_md(detected_ides))
                )
            ),
            instruction_targets=instruction_targets,
        )

    if client == "all":
        instruction_targets = _instruction_targets_from_clients(_INSTRUCTION_SYNC_CLIENT_IDS) if root_scope else ()
        return WriteTargetDecision(
            write_agents=config.agents_md_enabled and root_scope,
            instruction_targets=instruction_targets,
        )

    profile = resolve_client_profile(client)
    instruction_targets = _instruction_targets_from_clients((client,)) if root_scope else ()
    return WriteTargetDecision(
        write_agents=config.agents_md_enabled and root_scope and profile.write_targets.agents_md,
        instruction_targets=instruction_targets,
    )


def _sync_agents_md_if_needed(
    write_agents: bool,
    config: TRWConfig,
    project_root: Path,
    client: str = "auto",
    *,
    force: bool = False,
    dry_run: bool = False,
) -> tuple[bool, str | None, tuple[InstructionWriteVerdict, ...]]:
    """Write ``.trw/INSTRUCTIONS.md`` and AGENTS.md's link to it, if needed.

    Returns ``(synced, path, verdicts)``: the instructions-file verdict, then
    AGENTS.md's when it was attempted. Each carries the PRD-FIX-123 guard
    outcome so the dispatcher reports a refusal or a dry-run diff instead of
    silently claiming success. A refused instructions file stops AGENTS.md
    (PRD-CORE-341-FR03/FR07).
    """
    if not write_agents:
        return False, None, ()

    agents_target = project_root / "AGENTS.md"
    effective_client = client
    if client == "auto":
        detected_ides = detect_ide(project_root)
        if "codex" in detected_ides and "opencode" not in detected_ides:
            effective_client = "codex"

    # PRD-CORE-341: the body goes to the TRW-owned file; AGENTS.md gets the link.
    instructions = write_instructions_file(project_root, dry_run=dry_run, config=config, client=effective_client)
    if instructions.refusal is not None:
        return False, None, (instructions,)
    agents_section = agents_link_section()
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
        return False, None, (instructions,)  # block mode: oversize already logged by the gate; abort the write.

    verdict = merge_trw_section(
        agents_target,
        agents_section,
        config.max_auto_lines,
        force=force,
        dry_run=dry_run,
        config=config,
        project_root=project_root,
    )
    return verdict.written, str(agents_target), (instructions, verdict)


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
