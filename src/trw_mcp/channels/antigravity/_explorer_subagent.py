"""AG-02: trw-distill-explorer subagent generator.

# Managed by TRW — no trw_distill imports permitted.

Writes the Antigravity subagent surface via the FR01 format registry
(:func:`trw_mcp.agents.agent_formats.agent_format_for`), the same one the
eleven bundled specialists use — PRD-CORE-252 moved that surface to
``.agents/agents`` and trimmed the frontmatter to what
antigravity.google/docs/subagents documents (``name``, ``description``,
``model`` in ``{inherit, flash, pro}``); this generator previously kept its
own hardcoded ``.antigravitycli/agents`` path and undocumented
``temperature``/``max_turns``/``timeout_mins`` keys because it is dynamically
rendered from sidecar data rather than a static bundled file, so the FR01
landing did not reach it. There is no format left to hand-maintain here: the
content is authored in the bundle's claude-code dialect (a capability-tier
``model:`` token, ``{tool:trw_x}`` body placeholders) and passed through
:func:`trw_mcp.agents.tier_resolver.materialize_agent`, which resolves the
tier and reshapes the frontmatter for this client exactly as it does for the
bundled corpus.

Default tier: T1 (NOT T2 — audit P1-15, OQ-05 context isolation unconfirmed).
Operator may upgrade to T2/T3 via .trw/config.yaml: channels.ag02.tier. This
is the content-depth tier (T1/T2/T3), independent of the model *capability*
tier the frontmatter's ``model:`` line carries (see
:data:`_MODEL_CAPABILITY_TIER`).

Idempotent: skips rewrite if sidecar SHA unchanged since last write.
Fail-open on missing sidecar: writes placeholder subagent (P2-19 fix).

Jinja2-free — f-strings only. All template vars pre-rendered.
Post-write assertion: no ``{{ `` in output (P1-23).

PRD-DIST-2404 FR07-FR11, FR14, FR16-FR18. Format registry routing: PRD-CORE-252.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict

from trw_mcp.agents.agent_formats import agent_format_for
from trw_mcp.agents.tier_resolver import materialize_agent
from trw_mcp.channels._provenance import now_utc_iso8601
from trw_mcp.channels._state import ChannelState, read_state, state_path_for, write_state
from trw_mcp.channels._telemetry import append_channel_event
from trw_mcp.exceptions import AgentFormatError

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

_AGENT_STEM = "trw-distill-explorer"
_ANTIGRAVITY_CLIENT_ID = "antigravity-cli"

#: Repo-relative destination, read from the FR01 registry rather than
#: hardcoded — the same lookup :func:`materialize_agent`'s caller uses for the
#: eleven bundled specialists (PRD-CORE-252-FR03: ``.agents/agents``).
_AGENT_RELATIVE_PATH = agent_format_for(_ANTIGRAVITY_CLIENT_ID).destination_for(_AGENT_STEM)

_DEFAULT_TIER: str = "T1"

#: Capability tier for the frontmatter ``model:`` line, in the bundle's
#: vocabulary (``trw_mcp.agents.tier_resolver.KNOWN_TIERS``). Resolves to
#: ``flash`` for antigravity-cli — the fast/light model this explorer used
#: before (``gemini-2.5-flash``), now expressed as a tier the registry
#: translates instead of a literal model id the client's own docs do not
#: accept.
_MODEL_CAPABILITY_TIER = "local-small"

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
    "mcp_trw_trw_code_search",
]

# Mutation tools are prohibited (FR10). The antigravity-cli format registry
# entry drops the bundled ``tools`` key entirely (that client's documented
# frontmatter has no tool-grant field), so this list is no longer emitted --
# the read-only guarantee for THIS client is enforced by the body instructions
# below, not a host-declared grant. See CHANGELOG for the P1 note.
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


# PRD-CORE-239 FR01 removed the `channel-render` subcommand. This provenance
# header is written into the installed explorer subagent file (see
# `_AGENT_RELATIVE_PATH`), a permanent file in a LICENSED user's repo — the
# gate opens for them — so a dead command here fails for the paying caller
# and nobody else. That is the same asymmetry the `trw_entity_risk_map` fix
# names. Nothing reads `ChannelEntry.regenerate_cmd`, so no test could have
# caught it.
def _build_agent_content(
    *,
    tier: str,
    sidecar_data: dict[str, Any] | None,
    generated_at: str,
    sidecar_sha: str,
) -> str:
    """Build the full agent file content, translated for antigravity-cli.

    Authored in the bundle's claude-code dialect — a capability-tier
    ``model:`` token and ``{tool:trw_x}`` body placeholders — then passed
    through :func:`materialize_agent` so tool names and the frontmatter shape
    come from the FR01 registry. The bundled ``tools:`` grant below is kept
    for documentation parity with the bundled corpus; the registry drops it
    for this client (see :data:`_MUTATION_TOOLS`'s docstring for why that is
    safe here).

    All template variables are pre-rendered with concrete values before that
    translation; no unrendered Jinja2-style ``{{ }}`` remains in the output.

    Args:
        tier: Render tier (T1, T2, T3) — content depth, not the model tier.
        sidecar_data: Parsed sidecar or None for placeholder mode.
        generated_at: ISO timestamp string (minute-truncated).
        sidecar_sha: SHA of the sidecar (or "none" if absent).

    Returns:
        Full agent file content string, in antigravity-cli's frontmatter shape.
    """
    if sidecar_data is not None:
        hotspots: list[dict[str, Any]] = sidecar_data.get("hotspots", [])
        conventions: list[Any] = sidecar_data.get("conventions", [])
        table = _hotspot_table(hotspots)
        convs = _conventions_section(conventions)
    else:
        table = _placeholder_hotspot_table()
        convs = "_No convention data yet. Run trw-mcp update-project after distill._"

    bundled = f"""\
---
name: trw-distill-explorer
description: >
  Read-only risk-scored exploration agent with distill intelligence.
  This agent surfaces hotspot risk scores and edge cases before file edits.
tools:
  - mcp__trw__trw_recall
  - mcp__trw__trw_before_edit_hint
  - mcp__trw__trw_codebase_risk_report
  - mcp__trw__trw_code_search
model: {_MODEL_CAPABILITY_TIER}
---

<!-- TRW:PROVENANCE
generated_by: trw-mcp
channel_id: {AG02_CHANNEL_ID}
sha: {sidecar_sha}
ts: {generated_at}
tier: {tier}
regenerate: trw-mcp init-project --client antigravity-cli
-->

## Distill Intelligence — Codebase Hotspots ({tier})

Stay in **read-only** exploration mode. Do NOT edit files, run tests,
or call mutation tools. Surface risk data and evidence only.

Before reading any file, call `{{tool:trw_before_edit_hint}}` — its
`distill_hint` carries the importers, inferred tests and co-change neighbours
you need for risky callers and downstream dependencies.

### Top Hotspot Files

{table}

### Project Conventions

{convs}

### Workflow

1. Call `{{tool:trw_before_edit_hint}}` with the target file path.
2. Call `{{tool:trw_codebase_risk_report}}` for full risk analysis.
3. Read files with `read_file` / `read_many_files`, search with `grep_search`.
4. Call `{{tool:trw_recall}}` to check if the topic has been investigated before.
5. Report findings — do NOT propose edits unless explicitly asked.
"""
    return materialize_agent(bundled, client=_ANTIGRAVITY_CLIENT_ID)


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
    """Write the antigravity-cli explorer subagent (registry destination, see
    :data:`_AGENT_RELATIVE_PATH`).

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
    except (ValueError, AgentFormatError) as exc:
        # ValueError: an unknown capability tier (tier_resolver.resolve_tier).
        # AgentFormatError: the registry translation itself (agent_frontmatter).
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
    except Exception:  # justified: fail-open, state persistence must not block agent write
        log.debug("ag02_state_write_failed", state_file=str(state_file), exc_info=True)

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
    except Exception:  # justified: fail-open telemetry, agent write already completed
        log.debug("ag02_channel_event_failed", channel_id=AG02_CHANNEL_ID, exc_info=True)

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
