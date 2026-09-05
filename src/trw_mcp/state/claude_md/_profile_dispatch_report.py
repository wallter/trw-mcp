"""Read-only reporting helpers for the profile sync dispatcher.

Belongs to the ``_profile_dispatcher.py`` facade. Split out to keep the
dispatcher under the 350-line ceiling enforced by
``tests/test_module_loc_gate.py``. Neither helper writes anything.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts._ceremony import InstructionPointerSkipDict

logger = structlog.get_logger(__name__)


def _capability_parity_drift(write_agents: bool, client: str) -> list[str]:
    """Return capability-projection parity drift detail strings for the sync.

    PRD-CORE-218-FR06: surface capability/lifecycle/count drift loudly in the
    sync result. Returns an empty list when AGENTS.md is not written (no
    capability appendix is generated) or when the generated projection matches
    the resolved surface manifest. A non-empty list is the same drift that
    causes the capability block to be dropped from the generated instructions.
    """
    if not write_agents:
        return []
    from trw_mcp.bootstrap._client_integration_appendix import (
        build_client_integration_appendix,
    )

    surface_id = "codex" if client == "codex" else "agents"
    appendix = build_client_integration_appendix(surface_id)
    return [f.detail for f in appendix.parity_failures]


def _cache_hit_carrier_report(
    target: Path,
    write_claude: bool,
    config: TRWConfig,
    scope: str,
) -> tuple[str | None, list[InstructionPointerSkipDict] | None, str | None]:
    """Read-only carrier classification for the cache-hit path (PRD-CORE-203 FR07).

    No write happens on a cache hit, so this reports the carrier state of the
    CURRENT CLAUDE.md (``healed=False`` since nothing was modified). Returns
    ``(None, None, None)`` when CLAUDE.md is not a write target.
    """
    if not write_claude or not target.exists():
        return None, None, None
    from trw_mcp.models.config._profiles import resolve_client_profile
    from trw_mcp.state.claude_md._instruction_carrier import (
        CarrierMode,
        classify_instruction_file,
        resolve_carrier_mode,
    )

    classification = classify_instruction_file(target)
    mode = resolve_carrier_mode(
        classification,
        import_syntax=resolve_client_profile("claude-code").instruction_import_syntax,
        externalize=config.instruction_externalize,
        scope=scope,
    )
    if mode is CarrierMode.IMPORT:
        return mode.value, None, config.instruction_external_filename
    if mode is CarrierMode.POINTER_SKIP:
        skips: list[InstructionPointerSkipDict] = [
            {"path": str(target), "import_targets": list(classification.import_targets), "healed": False}
        ]
        return mode.value, skips, None
    return mode.value, None, None


__all__ = ["_cache_hit_carrier_report", "_capability_parity_drift"]
