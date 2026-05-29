"""AG-02: trw-distill-explorer subagent generator.

# Managed by TRW — no trw_distill imports permitted.

Writes ``.antigravitycli/agents/trw-distill-explorer.md`` with valid
YAML frontmatter matching the existing four Antigravity subagent format.

Default tier: T1 (NOT T2 — audit P1-15, OQ-05 context isolation unconfirmed).
Operator may upgrade to T2/T3 via .trw/config.yaml: channels.ag02.tier.

Tools list is enumerated individually (not wildcard) pending Gate G-02
(OQ-03 — wildcard mcp_trw_* confirmation). No mutation tools (FR10).

Idempotent: skips rewrite if sidecar SHA unchanged since last write.
Fail-open on missing sidecar: writes placeholder subagent (P2-19 fix).

Jinja2-free — f-strings only. All template vars pre-rendered.
Post-write assertion: no ``{{ `` in output (P1-23).

PRD-DIST-2404 FR07-FR11, FR14, FR16-FR18.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict

from trw_mcp.channels._provenance import now_utc_iso8601
from trw_mcp.channels._state import ChannelState, read_state, state_path_for, write_state
from trw_mcp.channels._telemetry import append_channel_event

log = structlog.get_logger(__name__)

__all__ = [
    "AG02_CHANNEL_ID",
    "AgentWriteResult",
    "generate_distill_explorer_agent",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AG02_CHANNEL_ID = "ag-02-distill-explorer-subagent"

_AGENT_RELATIVE_PATH = ".antigravitycli/agents/trw-distill-explorer.md"

_DEFAULT_TIER: str = "T1"

# Tools: enumerated individually per Gate G-02 (OQ-03 wildcard unconfirmed).
# NO mutation tools (FR10): write_file, edit_file, trw_deliver excluded.
_AGENT_TOOLS = [
    "read_file",
    "read_many_files",
    "glob",
    "grep_search",
    "list_directory",
    "mcp_trw_trw_recall",
    "mcp_trw_trw_before_edit_hint",
    "mcp_trw_trw_codebase_risk_report",
    "mcp_trw_trw_entity_risk_map",
    "mcp_trw_trw_code_search",
]

# Mutation tools are prohibited (FR10).
_MUTATION_TOOLS = frozenset({"write_file", "edit_file", "trw_deliver"})

_PLACEHOLDER_HOTSPOT_ROW = "| {path} | {score} | {churn} | {callers} |"

_PLACEHOLDER_ROWS = [
    _PLACEHOLDER_HOTSPOT_ROW.format(path="<path>", score="<score>", churn="<churn>", callers="<callers>")
    for _ in range(5)
]

_TEMPLATE_SENTINEL = "{{ "


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

_WriteStatus = Literal["written", "skipped_same_sha", "error"]


class AgentWriteResult(BaseModel):
    """Outcome of a single trw-distill-explorer.md write attempt."""

    model_config = ConfigDict(extra="forbid")

    channel_id: str
    status: _WriteStatus
    path: str | None = None
    tier_used: str | None = None
    bytes_written: int | None = None
    sidecar_sha: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Content builders
# ---------------------------------------------------------------------------


def _hotspot_table(hotspots: list[dict[str, Any]], count: int = 5) -> str:
    """Build compact hotspot table rows for the subagent body."""
    rows: list[str] = []
    rows.append("| File | Score | Churn | Callers |")
    rows.append("|------|-------|-------|---------|")
    top = hotspots[:count]
    if top:
        for h in top:
            path = str(h.get("file", h.get("path", "<unknown>")))
            score = h.get("risk_score", h.get("score", 0.0))
            churn = h.get("churn", h.get("churn_count", "N/A"))
            callers = h.get("caller_count", h.get("callers", "N/A"))
            score_str = f"{float(score):.2f}" if score else "N/A"
            rows.append(f"| `{path}` | {score_str} | {churn} | {callers} |")
    else:
        rows.extend(_PLACEHOLDER_ROWS)
    return "\n".join(rows)


def _placeholder_hotspot_table() -> str:
    """Return a placeholder hotspot table when sidecar absent (FR14, P2-19)."""
    rows: list[str] = [
        "| File | Score | Churn | Callers |",
        "|------|-------|-------|---------|",
    ]
    rows.extend(["| `<path>` | `<score>` | `<churn>` | `<callers>` |"] * 5)
    return "\n".join(rows)


def _conventions_section(conventions: list[Any], count: int = 3) -> str:
    """Build top-N conventions bullet list for T1 subagent body."""
    top = conventions[:count]
    if not top:
        return "_No convention data yet._"
    lines: list[str] = []
    for c in top:
        if isinstance(c, dict):
            text = c.get("text", c.get("description", str(c)))
        else:
            text = str(c)
        lines.append(f"- {text}")
    return "\n".join(lines)


def _build_agent_content(
    *,
    tier: str,
    sidecar_data: dict[str, Any] | None,
    generated_at: str,
    sidecar_sha: str,
) -> str:
    """Build the full agent file content (frontmatter + body).

    All template variables are pre-rendered with concrete values.
    No ``{{ }}`` placeholders remain in the output.

    Args:
        tier: Render tier (T1, T2, T3).
        sidecar_data: Parsed sidecar or None for placeholder mode.
        generated_at: ISO timestamp string (minute-truncated).
        sidecar_sha: SHA of the sidecar (or "none" if absent).

    Returns:
        Full agent file content string.
    """
    tools_yaml = "\n".join(f"  - {t}" for t in _AGENT_TOOLS)

    if sidecar_data is not None:
        hotspots: list[dict[str, Any]] = sidecar_data.get("hotspots", [])
        conventions: list[Any] = sidecar_data.get("conventions", [])
        table = _hotspot_table(hotspots)
        convs = _conventions_section(conventions)
    else:
        table = _placeholder_hotspot_table()
        convs = "_No convention data yet. Run trw-mcp update-project after distill._"

    body = f"""\
---
name: trw-distill-explorer
description: >
  Read-only risk-scored exploration agent with distill intelligence.
  For lightweight code search and file reading, use @trw-explorer instead.
  This agent surfaces hotspot risk scores and edge cases before file edits.
tools:
{tools_yaml}
model: gemini-2.5-flash
temperature: 0.1
max_turns: 20
timeout_mins: 15
---

<!-- TRW:PROVENANCE
generated_by: trw-mcp
channel_id: {AG02_CHANNEL_ID}
sha: {sidecar_sha}
ts: {generated_at}
tier: {tier}
regenerate: trw-mcp channel-render --channel {AG02_CHANNEL_ID}
-->

## Distill Intelligence — Codebase Hotspots ({tier})

Stay in **read-only** exploration mode. Do NOT edit files, run tests,
or call mutation tools. Surface risk data and evidence only.

Before reading any file, call `mcp_trw_trw_entity_risk_map` to identify
risky callers and downstream dependencies.

### Top Hotspot Files

{table}

### Project Conventions

{convs}

### Workflow

1. Call `mcp_trw_trw_before_edit_hint` with the target file path.
2. Call `mcp_trw_trw_codebase_risk_report` for full risk analysis.
3. Read files with `read_file` / `read_many_files`, search with `grep_search`.
4. Call `mcp_trw_trw_recall` to check if the topic has been investigated before.
5. Report findings — do NOT propose edits unless explicitly asked.
"""
    return body


# ---------------------------------------------------------------------------
# Template safety check (P1-23)
# ---------------------------------------------------------------------------


def _assert_no_template_vars(content: str) -> None:
    """Raise ValueError if unsubstituted Jinja2-style template vars found."""
    if _TEMPLATE_SENTINEL in content:
        raise ValueError(
            "Unsubstituted template variable found in AG-02 subagent content; aborting write to prevent broken output."
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_distill_explorer_agent(
    *,
    repo_root: Path,
    sidecar_data: dict[str, Any] | None,
    sidecar_sha: str | None,
    tier_override: str | None = None,
) -> AgentWriteResult:
    """Write ``.antigravitycli/agents/trw-distill-explorer.md``.

    Idempotent: skips rewrite if *sidecar_sha* matches last-write SHA (FR09).
    Fail-open on missing sidecar: writes placeholder subagent (FR14, P2-19).

    Args:
        repo_root: Repository root directory.
        sidecar_data: Parsed sidecar payload or None.
        sidecar_sha: Git SHA of the sidecar, or None if absent.
        tier_override: Force a specific tier (T1/T2/T3). Defaults to T1.

    Returns:
        AgentWriteResult describing the outcome.
    """
    tier = tier_override or _DEFAULT_TIER
    sha = sidecar_sha or "none"
    agent_path = repo_root / _AGENT_RELATIVE_PATH
    channels_dir = repo_root / ".trw" / "channels"
    state_file = state_path_for(AG02_CHANNEL_ID, channels_dir)

    # Idempotent skip: same SHA → no rewrite (FR09, AC06).
    existing_state = read_state(state_file)
    if existing_state is not None and existing_state.last_sidecar_sha == sha and sha != "none":
        log.debug(
            "ag02_subagent_skip",
            reason="same_sha",
            sha=sha,
            outcome="skipped_same_sha",
        )
        return AgentWriteResult(
            channel_id=AG02_CHANNEL_ID,
            status="skipped_same_sha",
            path=_AGENT_RELATIVE_PATH,
            sidecar_sha=sha,
        )

    generated_at = now_utc_iso8601()[:16]  # minute-truncated (e.g. 2026-05-28T12:34)

    try:
        content = _build_agent_content(
            tier=tier,
            sidecar_data=sidecar_data,
            generated_at=generated_at,
            sidecar_sha=sha,
        )
        # Post-render assertion: no unsubstituted template vars (P1-23).
        _assert_no_template_vars(content)
    except ValueError as exc:
        log.debug(
            "ag02_subagent_template_error",
            error=str(exc),
            outcome="error",
        )
        return AgentWriteResult(
            channel_id=AG02_CHANNEL_ID,
            status="error",
            error=str(exc),
        )

    try:
        agent_path.parent.mkdir(parents=True, exist_ok=True)
        agent_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        log.debug(
            "ag02_subagent_write_error",
            path=str(agent_path),
            error=str(exc),
            outcome="error",
        )
        return AgentWriteResult(
            channel_id=AG02_CHANNEL_ID,
            status="error",
            error=str(exc),
        )

    bytes_written = len(content.encode("utf-8"))

    # Persist updated channel state.
    seg_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    new_state = ChannelState(
        channel_id=AG02_CHANNEL_ID,
        last_render_tier=tier,
        last_render_bytes=bytes_written,
        last_render_tokens_est=len(content.split()),
        last_sidecar_sha=sha,
        segment_interior_sha256=seg_sha,
        last_render_ts=generated_at,
    )
    try:
        write_state(new_state, state_file)
    except Exception:
        pass  # fail-open on state write

    # Emit telemetry (fail-open).
    try:
        append_channel_event(
            channel_id=AG02_CHANNEL_ID,
            client="antigravity-cli",
            event_type="push_write",
            tier=tier,
            bytes_emitted=bytes_written,
            extra={"outcome": "written"},
        )
    except Exception:
        pass

    log.debug(
        "ag02_subagent_written",
        path=str(agent_path),
        tier=tier,
        bytes_written=bytes_written,
        outcome="written",
    )

    return AgentWriteResult(
        channel_id=AG02_CHANNEL_ID,
        status="written",
        path=_AGENT_RELATIVE_PATH,
        tier_used=tier,
        bytes_written=bytes_written,
        sidecar_sha=sha,
    )
