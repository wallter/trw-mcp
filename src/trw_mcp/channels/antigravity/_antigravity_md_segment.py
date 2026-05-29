"""AG-01: ANTIGRAVITY.md distill segment renderer.

# Managed by TRW — no trw_distill imports permitted.

Implements the ``ag-01-antigravity-md-distill`` channel using the shared
11-step renderer from PRD-DIST-2400 instruction_segment/_renderer.py.

Segment is placed between ``<!-- trw:distill:start -->`` and
``<!-- trw:distill:end -->`` markers in ANTIGRAVITY.md.

T1 default (top-5 hotspots, top-3 conventions, pull-more-detail callouts).
T0 fallback: single beacon comment.
6144-byte quota.

Jinja2-free — f-strings only (NFR04).

Gate documentation (operator tasks — not code gates):
  G-01: Verify .antigravitycli/settings.json is read by Antigravity CLI v0.48.9+.
        Run init-project, open an Antigravity session, confirm mcp_trw_* tools visible.
  G-02: Confirm .antigravitycli subagent tools: wildcard mcp_trw_* or enumerate.

PRD-DIST-2404 FR03-FR06, FR11-FR13, FR15, FR17-FR18.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from trw_mcp.channels._manifest_models import (
    ChannelEntry,
    ChannelStatus,
    ChannelSurface,
    CleanupAction,
    CleanupConfig,
    CleanupTrigger,
    HumanEditDetection,
    MarkersConfig,
    WriteStrategy,
)
from trw_mcp.channels.instruction_segment import (
    InstructionSegmentResult,
    render_instruction_segment,
)

log = structlog.get_logger(__name__)

__all__ = [
    "AG01_CHANNEL_ID",
    "AG01_DISTILL_BEGIN",
    "AG01_DISTILL_END",
    "SegmentRenderResult",
    "build_ag01_channel_entry",
    "render_antigravity_distill_segment",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AG01_CHANNEL_ID = "ag-01-antigravity-md-distill"
AG01_DISTILL_BEGIN = "<!-- trw:distill:start -->"
AG01_DISTILL_END = "<!-- trw:distill:end -->"

T0_BEACON = "<!-- trw:distill: enabled, run update-project to expand -->"

DEFAULT_QUOTA_BYTES = 6144
T1_HOTSPOT_COUNT = 5
T1_CONVENTION_COUNT = 3


# ---------------------------------------------------------------------------
# Result model (typed alias of InstructionSegmentResult for AG-01)
# ---------------------------------------------------------------------------

SegmentRenderResult = InstructionSegmentResult


# ---------------------------------------------------------------------------
# Table safety helpers (FR12)
# ---------------------------------------------------------------------------

_YAML_AMBIGUOUS_BARE = frozenset(
    {
        "true",
        "false",
        "yes",
        "no",
        "on",
        "off",
        "null",
        "~",
        "True",
        "False",
        "Yes",
        "No",
        "On",
        "Off",
        "Null",
    }
)


def _yaml_safe_cell(value: str) -> str:
    """Return a YAML-safe table cell value (FR12).

    Escapes pipe characters and backtick-quotes bare YAML-ambiguous values
    (booleans, null) and values that look like bare floats or integers.
    """
    value = value.replace("|", r"\|")
    if value in _YAML_AMBIGUOUS_BARE:
        return f"`{value}`"
    try:
        float(value)
        return f"`{value}`"
    except ValueError:
        pass
    return value


# ---------------------------------------------------------------------------
# Content builders
# ---------------------------------------------------------------------------


def _t0_beacon() -> str:
    """Return the T0 presence beacon (FR05, FR13)."""
    return T0_BEACON


def _format_hotspot_row(entry: dict[str, Any], idx: int) -> str:
    """Format a single hotspot as a markdown table row (FR05, FR12)."""
    path = _yaml_safe_cell(str(entry.get("file", entry.get("path", "<unknown>"))))
    score = entry.get("risk_score", entry.get("score", 0.0))
    churn = entry.get("churn", entry.get("churn_count", "N/A"))
    callers = entry.get("caller_count", entry.get("callers", "N/A"))
    score_str = _yaml_safe_cell(f"{float(score):.2f}" if score else "N/A")
    churn_str = _yaml_safe_cell(str(churn))
    caller_str = _yaml_safe_cell(str(callers))
    return f"| {idx} | `{path}` | {score_str} | {churn_str} | {caller_str} |"


def _format_convention(convention: str | dict[str, Any]) -> str:
    """Format a single coding convention bullet."""
    if isinstance(convention, dict):
        text = convention.get("text", convention.get("description", str(convention)))
    else:
        text = str(convention)
    return f"- {text}"


def _t1_content(sidecar_data: dict[str, Any]) -> str:
    """Build T1 segment content: hotspot table + conventions + callouts (FR05)."""
    hotspots: list[dict[str, Any]] = sidecar_data.get("hotspots", [])
    conventions: list[Any] = sidecar_data.get("conventions", [])

    top_spots = hotspots[:T1_HOTSPOT_COUNT]
    top_convs = conventions[:T1_CONVENTION_COUNT]

    lines: list[str] = []
    lines.append("## TRW Distill — Codebase Intelligence (T1)\n")

    lines.append("### Highest-Risk Files\n")
    lines.append("| # | File | Score | Churn | Callers |")
    lines.append("|---|------|-------|-------|---------|")
    if top_spots:
        for i, h in enumerate(top_spots, 1):
            lines.append(_format_hotspot_row(h, i))
    else:
        lines.append("| — | _No hotspot data yet_ | — | — | — |")

    lines.append("")
    lines.append("### Project Conventions\n")
    if top_convs:
        lines.extend(_format_convention(c) for c in top_convs)
    else:
        lines.append("_No convention data yet._")

    lines.append("")
    lines.append("### Pull More Detail\n")
    lines.append(
        "Call `mcp_trw_trw_codebase_risk_report` for full risk analysis, "
        "`mcp_trw_trw_before_edit_hint` before editing a file, or "
        "`mcp_trw_trw_entity_risk_map` to map risky callers."
    )

    return "\n".join(lines)


def _content_for_tier_factory(sidecar_data: dict[str, Any] | None) -> Any:
    """Return a content_for_tier callback bound to *sidecar_data*.

    T0: returns the single beacon comment.
    T1 (and T2/T3/T4): returns the full hotspot table + conventions.
    When sidecar absent: returns T0 beacon regardless of requested tier.
    """

    def content_for_tier(tier: str) -> str:
        if sidecar_data is None or tier == "T0":
            return _t0_beacon()
        return _t1_content(sidecar_data)

    return content_for_tier


# ---------------------------------------------------------------------------
# Template variable safety check (FR11, P1-23)
# ---------------------------------------------------------------------------

_TEMPLATE_SENTINEL = "{{ "


def _assert_no_template_vars(content: str, context: str) -> None:
    """Raise ValueError if content contains unsubstituted Jinja2 template vars.

    Args:
        content: Rendered string to check.
        context: Description for the error message.

    Raises:
        ValueError: If ``{{ `` substring found in *content*.
    """
    if _TEMPLATE_SENTINEL in content:
        raise ValueError(
            f"Unsubstituted template variable found in {context}; aborting write to prevent broken output."
        )


# ---------------------------------------------------------------------------
# ChannelEntry factory
# ---------------------------------------------------------------------------


def build_ag01_channel_entry(
    *,
    tier_default: str = "T1",
    ttl_commits: int = 50,
    ttl_days: int = 14,
    quota_total_bytes: int = DEFAULT_QUOTA_BYTES,
) -> ChannelEntry:
    """Build the canonical ChannelEntry for ag-01-antigravity-md-distill.

    Args:
        tier_default: Default render tier (T1 per PRD §2).
        ttl_commits: Staleness threshold in commits.
        ttl_days: Staleness threshold in days.
        quota_total_bytes: Maximum segment size in UTF-8 bytes.

    Returns:
        Configured ChannelEntry ready for render_instruction_segment().
    """
    return ChannelEntry(
        id=AG01_CHANNEL_ID,
        client="antigravity-cli",
        surface=ChannelSurface.ANTIGRAVITY_RULES_SEGMENT,
        telemetry_tag="ag01_antigravity_md_distill",
        file="ANTIGRAVITY.md",
        lock_file=".trw/channels/ag-01-antigravity-md-distill.lock",
        status=ChannelStatus.ACTIVE,
        write_strategy=WriteStrategy.MARKER_REPLACE,
        tier_default=tier_default,
        tier_min="T0",
        markers=MarkersConfig(start=AG01_DISTILL_BEGIN, end=AG01_DISTILL_END),
        ttl_commits=ttl_commits,
        ttl_days=ttl_days,
        quota_total_bytes=quota_total_bytes,
        human_edit_detection=HumanEditDetection.SHA256_SEGMENT,
        cleanup=CleanupConfig(
            trigger=CleanupTrigger.TTL_EXCEEDED,
            action=CleanupAction.CLEAR_SEGMENT,
        ),
        regenerate_cmd="trw-mcp channel-render --channel ag-01-antigravity-md-distill",
        description=(
            "ANTIGRAVITY.md distill segment — top-5 risk files and project "
            "conventions from the last trw-distill run. T1 default."
        ),
        sidecar_schema="risk-report-sidecar/v0",
        activation_gate=None,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_antigravity_distill_segment(
    *,
    repo_root: Path,
    sidecar_data: dict[str, Any] | None,
    sidecar_sha: str | None,
    tier_override: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> SegmentRenderResult:
    """Render and inject the AG-01 distill segment into ANTIGRAVITY.md.

    Fail-open on missing sidecar: writes T0 stub segment, returns
    status="written" with tier_used="T0" (FR13).

    Template variable safety check (P1-23) runs on rendered content before
    writing; aborts with status="error" if any ``{{ `` found.

    Args:
        repo_root: Repository root directory.
        sidecar_data: Parsed sidecar payload or None (renders T0 stub).
        sidecar_sha: Git SHA of the sidecar file.
        tier_override: Force a specific tier (T0/T1/T2).
        force: Skip TTL and conflict checks.
        dry_run: Return would-be content without writing.

    Returns:
        SegmentRenderResult (alias of InstructionSegmentResult).
    """
    tier = tier_override or "T1"
    if sidecar_data is None:
        tier = "T0"

    entry = build_ag01_channel_entry(tier_default=tier)
    content_cb = _content_for_tier_factory(sidecar_data)

    # Pre-render check: validate no template vars escape into any tier output.
    for check_tier in ("T0", "T1"):
        try:
            sample = content_cb(check_tier)
            _assert_no_template_vars(sample, f"AG-01 content_for_tier({check_tier!r})")
        except ValueError as exc:
            log.debug(
                "ag01_template_var_check_failed",
                tier=check_tier,
                error=str(exc),
                outcome="error",
            )
            return SegmentRenderResult(
                channel_id=AG01_CHANNEL_ID,
                status="error",
                error=str(exc),
            )

    return render_instruction_segment(
        entry=entry,
        repo_root=repo_root,
        sidecar_sha=sidecar_sha,
        content_for_tier=content_cb,
        force=force,
        dry_run=dry_run,
    )
