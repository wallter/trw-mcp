"""Tool-lifecycle/instructions section renderers.

PRD-CORE-149-FR01: extracted from ``_static_sections.py`` facade.
Houses: framework reference, closing reminder, Codex instructions,
OpenCode instructions, and the compatibility prompting-guide loader.
"""

from __future__ import annotations

import hashlib
import re

import structlog

# PRD-CORE-149-FR01: resolve ``get_config`` via the facade.
import trw_mcp.state.claude_md._static_sections as _facade
from trw_mcp.models.config._client_profile import ClientProfile
from trw_mcp.state.claude_md._renderer import SESSION_BOUNDARY_TEXT as _SESSION_BOUNDARY_TEXT
from trw_mcp.state.claude_md._renderer import ProtocolRenderer

_logger = structlog.get_logger(__name__)

# PRD-QUAL-104 FR02: the deliver-gate phrase every loaded/fallback body MUST
# contain so FR03 injection can never produce a gate-less instruction file.
DELIVER_GATE_PHRASE = "Do NOT call `trw_deliver` unless"

# PRD-QUAL-104 FR04: whole-line content-hash markers emitted ahead of the
# synced lifecycle block. Lint recomputes + compares (sha256 first-12-hex).
LIFECYCLE_SYNC_MARKER_PREFIX = "<!-- trw:lifecycle-sync:sha256-"

# PRD-QUAL-104 FR02 NFR02: last-known-good in-module fallback. Verbatim snapshot
# of the canonical tool-lifecycle body — MUST contain the deliver-gate phrase.
_FALLBACK_TOOL_LIFECYCLE = """# TRW Tool Lifecycle

## Core Mandates

**MUST call `trw_session_start()` as your absolute first action.** It loads prior learnings, active run state, and the operational protocol; without it you start from zero.

## Mandatory Tool Lifecycle

| Tool | When | Requirement |
|------|------|-------------|
| `trw_session_start()` | **First Action** | **MANDATORY.** Loads prior learnings and active run state. |
| `trw_learn(summary, detail)` | On discoveries | **REQUIRED** for non-obvious technical insights or gotchas. |
| `trw_checkpoint(message)` | After milestones | **REQUIRED.** Saves resume point for context compaction. |
| `trw_deliver()` | **Last Action** | **MANDATORY.** Persists your discoveries for future agents. |

## Delegation

Delegate to focused helpers when the harness supports it and file ownership is clear. When it does not, run the same shards sequentially. Delegation is an optimization — the invariant is focused context, explicit ownership, persisted findings, and final integration by the orchestrator.

## Deliver Gate (v26.2)

Do NOT call `trw_deliver` unless at least one of:
- (a) `trw_build_check` returned `build_check_result=pass`, **or**
- (b) `allow_unverified=true` and `unverified_reason` contains a valid, unexpired
  acceptable-failure record with `failed_command`, `residual_risk`, `owner`, and
  `expiry_iso`, **or**
- (c) an authorized operator/config override is recorded with technical rationale.

A review-verdict label or free-text reason alone is not an acceptable-failure record.
Under the default `deliver_gate_mode: block_coding` a missing build check blocks when the task type expects a build artifact (`coding`, `rca`, `eval`) OR when the session recorded modifications to at least `deliver_gate_unclassified_change_threshold` distinct files — so an unclassified or misclassified run that changed code still blocks. A run that modified nothing surfaces the missing-build warning as an advisory without requiring an exception record.
"""


def _read_bundled_surface(filename: str) -> str:
    """Read a bundled instruction surface from ``trw_mcp/data/surfaces``.

    Isolated for monkeypatching in tests (patch ``_read_bundled_surface`` to
    simulate a packaging anomaly and exercise the fail-open fallback).
    """
    from importlib.resources import files as pkg_files

    surface = pkg_files("trw_mcp.data") / "surfaces" / filename
    return surface.read_text(encoding="utf-8")


def load_tool_lifecycle() -> str:
    """Load the bundled ``tool-lifecycle.md`` body (PRD-QUAL-104 FR02).

    Fail-open (NFR02): any read/decode/packaging error falls back to the
    last-known-good in-module constant (which carries the deliver-gate phrase)
    and logs a warning rather than raising.
    """
    try:
        body = _read_bundled_surface("tool-lifecycle.md")
    except Exception:  # justified: fail-open — missing bundled resource must not break rendering
        _logger.warning("tool_lifecycle_surface_load_failed", exc_info=True)
        return _FALLBACK_TOOL_LIFECYCLE
    if DELIVER_GATE_PHRASE not in body:
        # Defensive: a corrupted bundle without the gate phrase would silently
        # produce a gate-less surface — prefer the known-good fallback.
        _logger.warning("tool_lifecycle_surface_missing_gate")
        return _FALLBACK_TOOL_LIFECYCLE
    return body


def bundled_lifecycle_hash_prefix() -> str:
    """Return the sha256 first-12-hex prefix of the loaded tool-lifecycle body.

    PRD-QUAL-104 FR04: emit and lint-recompute share this single helper so a
    clean tree never disagrees on the marker.
    """
    body = load_tool_lifecycle()
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def render_deliver_gate_statement() -> str:
    """Return the deliver-gate Markdown block derived from the bundled source.

    PRD-QUAL-104 FR03: the non-negotiable instruction-file text injected into
    every light-client surface. Sourced via the FR02 loader (fail-open
    fallback) so it always carries the deliver-gate phrase. Preceded by a
    whole-line content-hash sync marker (FR04) so the lint can verify freshness
    in light-client files too.
    """
    body = load_tool_lifecycle()
    # Extract just the "## Deliver Gate" section from the bundled body so the
    # light-client block stays focused; fall back to the whole body if the
    # heading shape changes (still carries the gate phrase).
    match = re.search(r"(?ms)^##\s+Deliver Gate.*?(?=\n##\s|\Z)", body)
    gate_section = match.group(0).rstrip("\n") if match else body.rstrip("\n")
    sync_marker = f"{LIFECYCLE_SYNC_MARKER_PREFIX}{bundled_lifecycle_hash_prefix()} -->"
    return (
        f"{sync_marker}\n"
        "\n"
        "## TRW Governance (non-negotiable)\n"
        "\n"
        "Call `trw_session_start()` first.\n"
        "\n" + gate_section + "\n"
    )


def render_framework_reference() -> str:
    """Render framework reference directive for CLAUDE.md."""
    renderer = ProtocolRenderer(client_profile=_facade.get_config().client_profile)
    return renderer.render_framework_reference()


#: PRD-CORE-247-FR02: a substitute for EVERY obligation the protocol labels
#: RIGID, not the two commands PRD-FIX-073-FR03 shipped.
#:
#: The old text named ``local init`` and ``local checkpoint`` only, so an agent
#: that lost the transport was told how to open a run and save progress and
#: nothing about recall, learning, feedback, build evidence, or delivery — the
#: three obligations with the highest cost of being skipped. Naming a partial
#: substitute set is what makes "improvise" the remaining option.
#:
#: The build-check row is a written artifact rather than a command, because the
#: offline path has no gate to evaluate: recording the command and its exit code
#: in ``reports/`` produces the same evidence a reviewer needs, in the same place
#: ``trw_build_check`` results are read from.
_OFFLINE_SUBSTITUTES = """### Troubleshooting: the MCP surface is absent

If the `trw_*` tools are missing or fail (`fetch failed`, a connect timeout, an
empty tool list), every obligation still binds — RIGID names an OBLIGATION, not a
tool call. Use the offline substitute:

| Obligation | Offline substitute |
|---|---|
| `trw_session_start` | `trw-mcp local status`, then `trw-mcp local recall --query "<domain>"` |
| `trw_init` | `trw-mcp local init --task NAME` |
| `trw_checkpoint` | `trw-mcp local checkpoint --message MSG` |
| `trw_learn` | `trw-mcp local learn --summary S --detail D --tag T` |
| `trw_recall` | `trw-mcp local recall --query Q` |
| `trw_build_check` | run the project-native check yourself, then write the exact command string and its integer exit code into the active run's `reports/` directory |
| `trw_deliver` | `trw-mcp local deliver --message MSG` — records `gate_evaluated: false`, which is an UNGATED delivery; the gate above still binds until evidence exists |
| Feedback | `trw-mcp local feedback --category C --subject S --message M` |

Writes made offline are marked (`source_identity=local_cli` plus a transient
`trw-reconcile-pending` tag) and the next successful `trw_session_start` reports
them back, so you do not have to track them by hand.
"""


def render_closing_reminder() -> str:
    """Render closing reminder with session boundaries and fallback guidance.

    PRD-FIX-073-FR03: includes local CLI fallback troubleshooting.
    PRD-CORE-247-FR02: that troubleshooting is now a substitute for every RIGID
    obligation rather than two of eight. This function is the INSTRUCTION-SURFACE
    arm of FR02 — the hook emits the same contract at the moment detection fires,
    and this puts it in a file the agent is already reading.
    PRD-QUAL-104 FR02: the deliver-gate language is derived from the bundled
    ``tool-lifecycle.md`` source (loaded via ``importlib.resources`` with
    fail-open fallback) rather than a hand-written copy, and a whole-line
    content-hash sync marker (FR04) precedes the synced block. It is stated in
    full exactly once here, which is this carrier's single statement (FR09).
    """
    return (
        render_deliver_gate_statement().rstrip("\n") + "\n"
        "\n"
        "### Session Boundaries\n"
        "\n" + _SESSION_BOUNDARY_TEXT + "\n" + _OFFLINE_SUBSTITUTES + "\n"
    )


def render_codex_instructions() -> str:
    """Render instructions content for Codex .codex/INSTRUCTIONS.md.

    PRD-QUAL-104 FR03: appends the non-negotiable session-start + deliver-gate
    block (bundled-source derived) so the Codex protocol carrier states the
    gate verbatim regardless of ceremony/deliver-gate config.

    Carries the FULL protocol — generic workflow and the client-integration
    appendix included — because this file is now codex's only TRW surface.

    PRD-QUAL-113-FR03 originally capped it at 2,025 bytes on the reasoning that
    "Codex deltas stay small; AGENTS.md owns generic workflow". That cap was a
    token-budget choice, not a vendor limit, and its premise was that AGENTS.md
    carried the rest. PRD-CORE-240-FR04 removes that premise: TRW no longer
    writes into codex's AGENTS.md, a file the user owns. With nothing else
    carrying the protocol, a cap that forces content OUT of the only carrier
    would push it nowhere.

    This file IS read: ``.codex/config.toml`` sets
    ``model_instructions_file = "INSTRUCTIONS.md"``, project-scoped
    ``.codex/config.toml`` is documented as supported, and relative paths
    "resolve from the config file that declares the role" — so it resolves to
    ``.codex/INSTRUCTIONS.md``. Corroborated by Codex's own documented rule that a
    Codex-relative path resolves from ``.codex/``.

    PRD-CORE-252 OQ-3 (resolved 2026-09-04): appends
    ``render_delegation_protocol()`` — a no-op string when
    ``include_delegation`` is False (opencode, cursor-cli), content when True
    (codex, on the byte measurement in ``_light_profile``'s docstring). This
    used to be the ONLY call site for ``render_delegation_protocol()`` in the
    codebase, which meant claude-code, cursor-ide, copilot, and
    antigravity-cli all had the flag True but never rendered the block — a
    wiring defect, not a deliberate scope choice. Every other client's
    renderer (``ProtocolRenderer.render_behavioral_protocol``,
    ``render_agents_trw_section``, ``render_antigravity_instructions``) now
    reaches the same gate through the same shared function.
    """
    from trw_mcp.state.claude_md.sections._delegation import (
        render_codex_trw_section,
        render_delegation_protocol,
    )

    return (
        "# Codex TRW Instructions\n"
        "\n"
        "## Instruction Sources\n"
        "\n"
        "- Codex layers global and project `AGENTS.md` guidance before work\n"
        "- TRW uses `.codex/INSTRUCTIONS.md` as the repo-local Codex instruction file\n"
        "- `.codex/agents/*.toml` custom agents are optional explicit helpers, not assumed background workers\n"
        "- Hooks are stable in current Codex but optional and trust-gated; correctness lives in TRW tools/middleware\n"
        "- Generic TRW lifecycle/project rules come from `AGENTS.md`; only Codex deltas live here\n"
        "\n"
        "## Runtime Guardrails\n"
        "\n"
        "- Prefer explicit file paths, concrete project-native verification steps, and small diffs\n"
        "- Follow TRW tool and middleware guidance even when no hook fires\n"
        "- If current Codex behavior matters, check the OpenAI developer docs before assuming runtime details\n"
        "\n"
        "## Key Gotchas\n"
        "\n"
        "- **Context limits vary**: avoid hardcoding a fixed Codex context budget in plans or prompts\n"
        "- **Hooks and nudges are optional**: treat them as additive hints, not correctness gates\n"
        "- **Instruction discovery**: `AGENTS.md` layering and `.codex/INSTRUCTIONS.md` serve different roles\n"
        "- **File navigation**: be explicit about file paths and the repo root you are changing\n"
        "\n" + render_deliver_gate_statement() + "\n" + render_codex_trw_section() + "\n" + render_delegation_protocol()
    )


def _load_prompting_guide(model_family: str) -> str:
    """Load a bundled prompting guide, falling back to portable guidance.

    ``model_family`` is retained for compatibility with existing OpenCode
    config detection, but v25 core guidance is capability-based and portable.
    """
    from importlib.resources import files as pkg_files

    filename = f"{model_family}.md" if model_family else "generic.md"
    try:
        data_path = pkg_files("trw_mcp.data") / "prompting" / filename
        return data_path.read_text(encoding="utf-8")
    except (OSError, FileNotFoundError, TypeError):
        try:
            data_path = pkg_files("trw_mcp.data") / "prompting" / "generic.md"
            return data_path.read_text(encoding="utf-8")
        except (OSError, FileNotFoundError, TypeError):
            return ""


def render_opencode_instructions(model_family: str) -> str:
    """Render portable instructions content for OpenCode.

    The ``model_family`` argument is accepted for compatibility with existing
    detection code, but the emitted v25 instructions are model agnostic.
    """
    renderer = ProtocolRenderer(
        client_profile=ClientProfile(client_id="opencode", display_name="opencode"),
        model_family=model_family,
    )
    return renderer.render_opencode_instructions()
