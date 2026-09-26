"""Shared contract for MCP-tool-replacement CLI commands (PRD-CORE-300-FR02 slice S0).

PRD-CORE-300 moves rarely used MCP tools to ``trw-mcp <subcommand>`` CLI verbs
(S1-S6: ``delivery recover``, ``probe run|budget``, ``meta-tune propose|rollback``,
``telemetry ...``, ``code index|risk``, ``prd create|diff``, ``run adopt``,
``instructions sync``, ``profile explain``). This module is the ONE registry
every later slice adds to, and the ONE guard every state-changing entry inherits
by being listed here rather than by re-implementing the refusal itself.

``CliReplacement.replaces`` is the sole place — besides a negative test — where a
removed tool's name may reappear in source: it exists so an operator reading the
registry can trace a CLI verb back to the tool it took over from, without that
name leaking into any *rendered* (agent-facing) text.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CliReplacement:
    """One MCP-tool-replacement CLI command and the contract it must meet.

    ``command`` is the full verb path as typed on the CLI, e.g. ``"local deliver"``
    (never including ``trw-mcp`` itself). ``replaces`` names the MCP tool this
    command took over from, or ``""`` when the command replaces no tool (S0's
    ``local deliver`` predates the cut; it is enrolled in the contract, not
    born from it).
    """

    command: str
    replaces: str
    state_changing: bool
    summary: str


#: S0 holds only commands that exist today and belong under the contract.
#: Later slices (S1-S6) append entries here; nothing else changes.
CLI_REPLACEMENTS: tuple[CliReplacement, ...] = (
    CliReplacement(
        command="code index",
        replaces="trw_code_index_update",
        state_changing=True,
        summary="Build the local code index that search and symbol lookup read.",
    ),
    CliReplacement(
        command="code risk",
        replaces="trw_codebase_risk_report",
        state_changing=False,
        summary="Read the ranked file-level composite-risk report for the current SHA.",
    ),
    CliReplacement(
        command="delivery recover",
        replaces="trw_delivery_recover",
        state_changing=True,
        summary="Recover a stale or crashed delivery with its capability token and expected revision.",
    ),
    CliReplacement(
        command="local deliver",
        replaces="",
        state_changing=True,
        summary="Offline delivery acceptance without MCP transport or gate evaluation.",
    ),
    CliReplacement(
        command="probe run",
        replaces="trw_probe",
        state_changing=True,
        summary="Run a sandboxed experiment against a plan assumption; spends the run's probe budget.",
    ),
    CliReplacement(
        command="probe budget",
        replaces="trw_probe_budget_status",
        state_changing=False,
        summary="Report a run's probe budget usage.",
    ),
    CliReplacement(
        command="meta-tune propose",
        replaces="trw_meta_tune_propose",
        state_changing=True,
        summary="Promote a SAFE-001 candidate through sandbox and review (Linux only).",
    ),
    CliReplacement(
        command="memory reembed",
        replaces="",
        state_changing=True,
        summary="Re-encode this checkout's stored vectors that are outside the daemon's active embedding space.",
    ),
    CliReplacement(
        command="meta-tune rollback",
        replaces="trw_meta_tune_rollback",
        state_changing=True,
        summary="Restore a promoted SAFE-001 proposal's prior content.",
    ),
    # S3a: all five are read-only queries over already-persisted state.
    CliReplacement(
        command="telemetry events",
        replaces="trw_query_events",
        state_changing=False,
        summary="Merged cross-emitter event view for a session.",
    ),
    CliReplacement(
        command="telemetry classify",
        replaces="trw_surface_classify",
        state_changing=False,
        summary="Classify a path as SAFE-001 control or advisory.",
    ),
    CliReplacement(
        command="telemetry surface-diff",
        replaces="trw_surface_diff",
        state_changing=False,
        summary="Structured diff between two recorded surface snapshots.",
    ),
    CliReplacement(
        command="telemetry security",
        replaces="trw_mcp_security_status",
        state_changing=False,
        summary="MCP registered-server / allowlist / anomaly / quarantine status.",
    ),
    CliReplacement(
        command="telemetry channel-stats",
        replaces="trw_channel_stats",
        state_changing=False,
        summary="Per-channel push->outcome correlation rate.",
    ),
    CliReplacement(
        command="prd create",
        replaces="trw_prd_create",
        state_changing=True,
        summary="Generate an AARE-F PRD from a feature description and write it to disk.",
    ),
    CliReplacement(
        command="prd diff",
        replaces="trw_prd_diff",
        state_changing=False,
        summary="Diff two PRD files, focused on requirements, metrics, and acceptance gates.",
    ),
    # S11b: read-only; trw_status(detail="surface") returns the same payload over MCP.
    CliReplacement(
        command="profile explain",
        replaces="trw_profile_explain",
        state_changing=False,
        summary="Show which config layer set each profile field, and the resolved tool surface.",
    ),
    CliReplacement(
        command="telemetry pipeline-health",
        replaces="trw_pipeline_health",
        state_changing=False,
        summary="Report the four compounding-pipeline health signals (sync, graph, embeddings, recall).",
    ),
    CliReplacement(
        command="run adopt",
        replaces="trw_adopt_run",
        state_changing=True,
        summary="Transfer an existing run's pin to a named session.",
    ),
    CliReplacement(
        command="instructions sync",
        replaces="trw_instructions_sync",
        state_changing=True,
        summary="Sync TRW protocol + ceremony guidance into the client's instruction file.",
    ),
)


def cli_replacement_families() -> tuple[str, ...]:
    """Unique command families across the registry, first-appearance order.

    A "family" is the first word of an entry's ``command`` path, e.g. both
    ``"code index"`` and ``"code risk"`` belong to family ``"code"``. Shared by
    :func:`render_cli_replacements_pointer` and its coverage tests so the
    collapsed pointer and the "every entry is represented" check can never
    drift apart.
    """
    families: list[str] = []
    for entry in CLI_REPLACEMENTS:
        family = entry.command.split(" ", 1)[0]
        if family not in families:
            families.append(family)
    return tuple(families)


def render_cli_replacements_pointer() -> str:
    """One compact line pointing at ``trw-mcp --help`` for CLI-replacement commands.

    Names each command *family* once (e.g. "telemetry", "prd") rather than
    enumerating every ``trw-mcp <verb> --help`` pointer. Rendered FROM the
    registry (never hand-listed) so a future S1-S6 entry's family shows up
    here automatically. Collapsed 2026-09-25 (PRD-CORE-300-FR02): a full
    20-verb enumeration regressed the context-cost gate
    (docs/sprint-mcp7/context-cost-baseline-2026-09-24.json) across every
    client that renders server instructions or the client-integration
    appendix; per-verb ``--help`` is still one `trw-mcp --help` away. Returns
    "" when the registry is empty so callers can skip an empty sentence.
    """
    families = cli_replacement_families()
    if not families:
        return ""
    return f"Some tools moved to the CLI: `trw-mcp --help` (families: {', '.join(families)})."


def find_cli_replacement(command_path: str) -> CliReplacement | None:
    """Return the registry entry whose ``command`` equals *command_path*, if any."""
    for entry in CLI_REPLACEMENTS:
        if entry.command == command_path:
            return entry
    return None


def invoked_command_path(cmd: str, args: object) -> str:
    """Reconstruct the full verb path actually invoked, e.g. ``"local deliver"``.

    Follows the repo's existing subparser ``dest`` convention (``auth_command``,
    ``local_command``, ...): dest is ``f"{verb}_command"`` for the immediate
    child of each ``add_subparsers()`` call. Walking that convention keeps this
    generic across every S1-S6 verb group without a second parallel mapping to
    drift from the argparse tree in ``_cli_argparse*.py``.
    """
    parts = [cmd]
    current = cmd
    while True:
        attr = f"{current.replace('-', '_')}_command"
        value = getattr(args, attr, None)
        if not isinstance(value, str) or not value:
            break
        parts.append(value)
        current = value
    return " ".join(parts)


def enforce_state_changing_guard(cmd: str, args: object) -> None:
    """Refuse a state-changing CLI-replacement command in a reviewer/dispatched-child process.

    Applied ONCE, at CLI dispatch (``_cli.py::main``), ahead of every handler —
    a future registry entry inherits the refusal just by being listed with
    ``state_changing=True``; it never needs its own check. Exits the process
    (never raises) so it composes with the existing ``SUBCOMMAND_HANDLERS``
    dispatch, which does not otherwise expect a return value here.
    """
    entry = find_cli_replacement(invoked_command_path(cmd, args))
    if entry is None or not entry.state_changing:
        return

    from trw_mcp.dispatch._child_marker import dispatched_child_active
    from trw_mcp.state._surface_role import reviewer_role_active

    if reviewer_role_active():
        logger.warning("cli_replacement_refused_reviewer_role", command=entry.command)
        print(
            f"Refused: '{entry.command}' is state-changing; TRW_SURFACE_ROLE=reviewer forbids it.",
            file=sys.stderr,
        )
        sys.exit(1)
    if dispatched_child_active():
        logger.warning("cli_replacement_refused_dispatched_child", command=entry.command)
        print(
            f"Refused: '{entry.command}' is state-changing; TRW_DISPATCH_CHILD forbids it in a dispatched child.",
            file=sys.stderr,
        )
        sys.exit(1)


__all__ = [
    "CLI_REPLACEMENTS",
    "CliReplacement",
    "cli_replacement_families",
    "enforce_state_changing_guard",
    "find_cli_replacement",
    "invoked_command_path",
    "render_cli_replacements_pointer",
]
