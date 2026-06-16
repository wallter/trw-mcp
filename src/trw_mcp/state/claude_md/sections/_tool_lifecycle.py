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

## Deliver Gate (v26)

Do NOT call `trw_deliver` unless at least one of:
- (a) `trw_build_check` returned `build_check_result=pass`, **or**
- (b) a `review_verdict` carries an explicit `acceptable-failure` label, **or**
- (c) an explicit override justification is included in the deliver message.

For task types `coding`, `rca`, `eval` the gate blocks by default (`deliver_gate_mode: block_coding`). Docs, research, planning, and unknown types remain advisory.
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


def render_closing_reminder() -> str:
    """Render closing reminder with session boundaries and fallback guidance.

    PRD-FIX-073-FR03: Includes local CLI fallback troubleshooting.
    PRD-QUAL-104 FR02: the deliver-gate language is now derived from the
    bundled ``tool-lifecycle.md`` source (loaded via ``importlib.resources``
    with fail-open fallback) rather than a hand-written copy, and a whole-line
    content-hash sync marker (FR04) precedes the synced block.
    """
    return (
        render_deliver_gate_statement().rstrip("\n") + "\n"
        "\n"
        "### Session Boundaries\n"
        "\n" + _SESSION_BOUNDARY_TEXT + "\n"
        "### Troubleshooting\n"
        "\n"
        "If MCP tools fail with 'fetch failed', use the local CLI fallback:\n"
        "- `trw-mcp local init --task NAME` to create a run directory\n"
        "- `trw-mcp local checkpoint --message MSG` to save progress\n"
        "\n"
    )


def render_codex_instructions() -> str:
    """Render instructions content for Codex .codex/INSTRUCTIONS.md.

    PRD-QUAL-104 FR03: appends the non-negotiable session-start + deliver-gate
    block (bundled-source derived) so the Codex protocol carrier states the
    gate verbatim regardless of ceremony/deliver-gate config.
    """
    return (
        "# Codex TRW Instructions\n"
        "\n"
        "## Instruction Sources\n"
        "\n"
        "- Codex reads `AGENTS.md` files before work, layering global and project guidance by directory precedence\n"
        "- TRW uses `.codex/INSTRUCTIONS.md` as the repo-local Codex instruction file\n"
        "- `.codex/agents/*.toml` custom agents are optional explicit helpers, not assumed background workers\n"
        "- Codex hooks are experimental and optional; core TRW correctness lives in the tools and middleware\n"
        "\n"
        "## Codex Workflow\n"
        "\n"
        "1. **Start**: call `trw_session_start()` — loads prior learnings and any active run\n"
        "2. **Delegate**: use custom agents or subagents only when you explicitly ask Codex to spawn them\n"
        "3. **Verify**: keep the working set small and run project-native checks after meaningful changes\n"
        "4. **Learn**: Call `trw_learn()` for reusable gotchas or patterns\n"
        "5. **Finish**: call `trw_deliver()` — persists work for future sessions\n"
        "\n"
        "## Ceremony Protocol\n"
        "\n"
        "- `trw_checkpoint(message)` — saves progress so you can resume after context compaction\n"
        "- `trw_learn(summary, detail)` — record durable technical discoveries (no status reports)\n"
        "- `trw_deliver()` — persists everything in one call when done\n"
        "\n"
        "## Runtime Guardrails\n"
        "\n"
        "- Prefer explicit file paths, concrete project-native verification steps, and small diffs\n"
        "- Use custom agents or subagents only when you explicitly ask Codex to spawn them\n"
        "- Follow TRW tool and middleware guidance even when no hook fires\n"
        "- If current Codex behavior matters, check the OpenAI developer docs before assuming runtime details\n"
        "\n"
        "## Key Gotchas\n"
        "\n"
        "- **Context limits vary**: avoid hardcoding a fixed Codex context budget in plans or prompts\n"
        "- **Hooks and nudges are optional**: treat them as additive hints, not correctness gates\n"
        "- **Instruction discovery**: `AGENTS.md` layering and `.codex/INSTRUCTIONS.md` serve different roles\n"
        "- **File navigation**: be explicit about file paths and the repo root you are changing\n"
        "\n" + render_deliver_gate_statement()
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
