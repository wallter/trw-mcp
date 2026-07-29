"""Codex distill channels — the PostToolUse telemetry hook.

# Managed by TRW — no trw_distill imports permitted.

One active channel: `codex-posttooluse-telemetry` (hook_script). It is
distill-FREE — it emits telemetry and imports nothing from the proprietary
package, which is why PRD-CORE-239 §3b preserved it while removing its former
sibling `codex-agents-md-hotspots` (an AGENTS.md instruction segment that never
rendered).

T2 tool-return enrichment is delivered by the shared `enrich_response` /
`_tool_return_tiers` substrate path, not by a codex-specific builder.

PRD-DIST-2402's telemetry half survives; its segment half does not.
"""

from __future__ import annotations

from trw_mcp.channels.codex._post_tool_use_telemetry import install_hook_script

__all__ = ["install_hook_script"]
