"""Default ChannelEntry factory functions for CUR-01 through CUR-05.

Extracted from _mdc_emitter.py to satisfy the 350 effective-LOC gate.
These entries are bootstrapped into .trw/channels/manifest.yaml by
MdcEmitter.bootstrap_stubs().

PRD-DIST-2401 §6.3 Manifest Entries.
"""

from __future__ import annotations

from trw_mcp.channels._manifest_models import (
    ChannelEntry,
    ChannelStatus,
    ChannelSurface,
    CleanupAction,
    CleanupConfig,
    CleanupTrigger,
    HumanEditDetection,
    MarkersConfig,
    ProvenanceConfig,
    WriteStrategy,
)

__all__ = [
    "DEFAULT_ENTRIES",
    "REGEN_CMD",
    "make_cur01_entry",
    "make_cur02_entry",
    "make_cur03_entry",
    "make_cur04_entry",
    "make_cur05_entry",
]

REGEN_CMD = "trw-distill self-improve mdc-emit"
_CUR04_CHANNEL_ID = "cursor-mcp-tool-return"


def make_cur01_entry() -> ChannelEntry:
    """CUR-01: distill-conventions.mdc — git_tracked: true (P1-08 fix)."""
    return ChannelEntry(
        id="cursor-mdc-conventions",
        client="cursor-ide",
        surface=ChannelSurface.CURSOR_MDC_FILE,
        telemetry_tag="cursor.mdc.conventions",
        file=".cursor/rules/distill-conventions.mdc",
        lock_file=".trw/channels/cursor-mdc-conventions.lock",
        status=ChannelStatus.ACTIVE,
        write_strategy=WriteStrategy.FULL_REWRITE,
        tier_default="T1",
        tier_min="T0",
        distill_record_types=["convention", "hotspot"],
        ttl_commits=50,
        ttl_days=30,
        quota_total_bytes=4096,
        mdc_description="TRW distill: project coding conventions and hotspot risk summary",
        mdc_globs="[]",
        mdc_always_apply=False,
        regenerate_cmd=REGEN_CMD,
        description="CUR-01: conventions.mdc — git_tracked: true (P1-08 fix)",
        provenance=ProvenanceConfig(enabled=True, detection=HumanEditDetection.SHA256_SEGMENT),
        cleanup=CleanupConfig(trigger=CleanupTrigger.TTL_EXCEEDED, action=CleanupAction.TOMBSTONE),
    )


def make_cur02_entry() -> ChannelEntry:
    """CUR-02: distill-hotspots-{dir}.mdc template — git_tracked: false."""
    return ChannelEntry(
        id="cursor-mdc-hotspots-template",
        client="cursor-ide",
        surface=ChannelSurface.CURSOR_MDC_FILE,
        telemetry_tag="cursor.mdc.hotspots",
        file=None,
        lock_file=".trw/channels/cursor-mdc-hotspots.lock",
        status=ChannelStatus.ACTIVE,
        write_strategy=WriteStrategy.FULL_REWRITE,
        tier_default="T1",
        tier_min="T0",
        distill_record_types=["edge_case_survivor", "edge_case_undocumented", "hotspot"],
        ttl_commits=30,
        ttl_days=14,
        quota_total_bytes=3072,
        mdc_always_apply=False,
        regenerate_cmd=REGEN_CMD,
        description="CUR-02: hotspots-{dir}.mdc — git_tracked: false (P1-08); max_instantiations=12",
        provenance=ProvenanceConfig(enabled=True, detection=HumanEditDetection.SHA256_SEGMENT),
        cleanup=CleanupConfig(trigger=CleanupTrigger.TTL_EXCEEDED, action=CleanupAction.TOMBSTONE),
    )


def make_cur03_entry() -> ChannelEntry:
    """CUR-03: distill-dangerous-edits.mdc — git_tracked: false."""
    return ChannelEntry(
        id="cursor-mdc-dangerous-edits",
        client="cursor-ide",
        surface=ChannelSurface.CURSOR_MDC_FILE,
        telemetry_tag="cursor.mdc.dangerous_edits",
        file=".cursor/rules/distill-dangerous-edits.mdc",
        lock_file=".trw/channels/cursor-mdc-dangerous-edits.lock",
        status=ChannelStatus.ACTIVE,
        write_strategy=WriteStrategy.FULL_REWRITE,
        tier_default="T1",
        tier_min="T0",
        distill_record_types=["edge_case_survivor", "edge_case_undocumented"],
        ttl_commits=40,
        ttl_days=21,
        quota_total_bytes=5120,
        mdc_always_apply=False,
        regenerate_cmd=REGEN_CMD,
        description="CUR-03: dangerous-edits.mdc — git_tracked: false",
        provenance=ProvenanceConfig(enabled=True, detection=HumanEditDetection.SHA256_SEGMENT),
        cleanup=CleanupConfig(trigger=CleanupTrigger.TTL_EXCEEDED, action=CleanupAction.TOMBSTONE),
    )


def make_cur04_entry() -> ChannelEntry:
    """CUR-04: cursor-mcp-tool-return — telemetry only, no file written."""
    return ChannelEntry(
        id=_CUR04_CHANNEL_ID,
        client="cursor-ide",
        surface=ChannelSurface.EPHEMERAL_STDOUT,
        telemetry_tag="cursor.mcp.tool_return",
        file=None,
        lock_file=None,
        status=ChannelStatus.ACTIVE,
        write_strategy=WriteStrategy.NONE,
        tier_default="T2",
        tier_min="T0",
        distill_record_types=["edge_case_survivor", "edge_case_undocumented", "file_risk_score"],
        regenerate_cmd=None,
        description="CUR-04: tool-return enrichment via before_edit_hint; no file written",
    )


def make_cur05_entry() -> ChannelEntry:
    """CUR-05: cursor-cli AGENTS.md marker-replace segment."""
    return ChannelEntry(
        id="cursor-cli-agents-md-snapshot",
        client="cursor-cli",
        surface=ChannelSurface.AGENTS_MD_SEGMENT,
        telemetry_tag="cursor.cli.agents_md",
        file="AGENTS.md",
        lock_file=".trw/channels/cursor-cli-agents-md.lock",
        status=ChannelStatus.ACTIVE,
        write_strategy=WriteStrategy.MARKER_REPLACE,
        tier_default="T1",
        tier_min="T0",
        distill_record_types=["convention", "hotspot", "edge_case_survivor"],
        ttl_commits=30,
        ttl_days=14,
        quota_total_bytes=1536,
        mdc_always_apply=False,
        regenerate_cmd=REGEN_CMD,
        description="CUR-05: cursor-cli AGENTS.md T1 segment",
        markers=MarkersConfig(
            start="<!-- TRW:DISTILL:BEGIN -->",
            end="<!-- TRW:DISTILL:END -->",
        ),
        provenance=ProvenanceConfig(enabled=True, detection=HumanEditDetection.MARKER_BOUNDARY),
        cleanup=CleanupConfig(trigger=CleanupTrigger.TTL_EXCEEDED, action=CleanupAction.CLEAR_SEGMENT),
    )


# Pre-built defaults for fast lookup in MdcEmitter
DEFAULT_ENTRIES: list[ChannelEntry] = [
    make_cur01_entry(),
    make_cur02_entry(),
    make_cur03_entry(),
    make_cur04_entry(),
    make_cur05_entry(),
]
