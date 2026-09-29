"""The one description string per registered tool (PRD-INFRA-195-FR01).

Each registered tool has exactly one summary, kept here and nowhere else. It
reaches every reader unchanged:

* the model: ``server/_tool_summaries.py`` makes it the first paragraph of the
  tool's served MCP description at boot; the tool's docstring supplies the rest
  (the ``Use when`` trigger and the output contract, PRD-QUAL-074);
* instruction files: ``state/claude_md/_tool_manifest.render_tool_list``;
* ``/docs/tools``: ``scripts/generate-inventory.py`` reads the served
  description off the production app, so the page shows what the model reads.

Like ``surface_packs.py`` this module is pure (no imports), so the state layer
reads it without booting the server. Keys equal the registered surface; the
instruction manifest checks that at import. Each summary is one or two
sentences of at most 240 characters (PRD-INFRA-195-NFR01); parameter detail
belongs in the input schema, and anything a caller needs to choose the tool
belongs in the docstring body. Order is the order instruction files list
tools in.
"""

from __future__ import annotations

TOOL_SUMMARIES: dict[str, str] = {
    "trw_session_start": "Load prior learnings and any active run so you start with full context.",
    "trw_checkpoint": "Append a progress snapshot so work survives context compaction.",
    "trw_learn": "Record or correct a durable technical discovery.",
    "trw_deliver": "Persist learnings and progress so future sessions inherit this session's work.",
    "trw_recall": "Retrieve prior learnings, or with graph_id=<id> that learning's graph neighbours.",
    "trw_build_check": "Record build/test results for ceremony tracking and delivery gates.",
    "trw_review": "Compute a pass/warn/block review verdict and persist review.yaml.",
    "trw_prd_validate": "Score a PRD against the validation suite; returns a READY/NEEDS-WORK verdict.",
    "trw_status": "Report the active run's phase, progress and last activity.",
    "trw_init": "Create a run directory and register it as the active run.",
    "trw_dispatch": "Delegate a prompt to another agent CLI, or read a job or evidence.",
    "trw_code": "Find a symbol's definition in the local code index, or get before-edit hints.",
    "trw_send": "Send a bounded, durable message to a formation peer (pull-only).",
    "trw_inbox": "Fetch, acknowledge or inspect peer messages, or run a peer action such as enroll or heartbeat.",
    "trw_assess": "Ask an opt-in calibrated judge typed questions about a state and get advisory probabilities.",
}

__all__ = ["TOOL_SUMMARIES"]
