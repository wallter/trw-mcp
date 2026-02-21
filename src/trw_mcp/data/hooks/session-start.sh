#!/bin/sh
# PRD-INFRA-002-FR01/FR02/FR03/FR04: Unified SessionStart hook.
# Dispatches on $SOURCE (startup|resume|compact|clear) from stdin JSON.
# Framing: value-oriented — explains what each tool gives the agent.
# Research: Anthropic context engineering, motivation framing, self-interest framing.
# Fail-open: any error silently exits 0.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

# Read stdin payload to determine source
_payload=$(cat) || exit 0
_source=""
if command -v jq >/dev/null 2>&1; then
  _source=$(printf '%s' "$_payload" | jq -r '.source // empty' 2>/dev/null) || true
fi
# Fallback: extract source via grep
if [ -z "$_source" ]; then
  _source=$(printf '%s' "$_payload" | grep -o '"source"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"source"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
fi

_project_root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0

# --- Value-oriented protocol summary ---
_emit_protocol() {
  _protocol_file="$_project_root/.trw/context/behavioral_protocol.yaml"
  if [ -f "$_protocol_file" ]; then
    echo "TRW PROTOCOL — tools that help you build effectively:"
    grep '^ *-' "$_protocol_file" | sed 's/^ *- *//;s/^"//;s/"$//'
  else
    echo "TRW PROTOCOL — tools that help you build effectively:"
    echo "- trw_session_start(): loads learnings + recovers active run"
    echo "- trw_checkpoint(): saves progress so you resume after compaction"
    echo "- trw_learn(): records discoveries for future sessions"
    echo "- trw_deliver(): persists everything in one call when done"
  fi
}

case "$_source" in
  startup)
    # FR01: Fresh startup — explain what's available and why
    _emit_protocol
    echo ""
    echo ""
    echo "YOUR ROLE: Orchestrate, delegate, verify, and preserve knowledge."
    echo "For non-trivial tasks (2+ files), delegate to Agent Teams or subagents — focused context produces better outcomes than direct implementation."
    echo "As orchestrator: assess scope, dispatch agents, monitor progress, verify integration, run quality gates."
    echo ""
    echo "Call trw_session_start() to load your learnings and any active run state."
    ;;

  resume)
    # FR02: Resume — brief, goal-oriented
    _emit_protocol
    echo ""
    echo "SESSION RESUMED — your run state and learnings are preserved."
    echo "Call trw_status() to see where you left off and what to work on next."
    ;;

  compact)
    # FR03: Compaction recovery — emphasize progress is safe, show recovered state
    echo "CONTEXT COMPACTED — your conversation was compressed but your implementation progress is safe."
    echo ""
    _emit_protocol
    echo ""
    # Recover pre-compaction state if available
    _state_file="$_project_root/.trw/context/pre_compact_state.json"
    if [ -f "$_state_file" ] && command -v jq >/dev/null 2>&1; then
      _run_path=$(jq -r '.run_path // empty' "$_state_file" 2>/dev/null) || true
      _phase=$(jq -r '.phase // empty' "$_state_file" 2>/dev/null) || true
      _event_count=$(jq -r '.events_logged // 0' "$_state_file" 2>/dev/null) || true
      _last_cp=$(jq -r '.last_checkpoint // empty' "$_state_file" 2>/dev/null) || true
      if [ -n "$_run_path" ]; then
        echo "RECOVERED: Run at $_run_path"
        [ -n "$_phase" ] && echo "RECOVERED: Phase: $_phase | Events: ${_event_count:-0}"
        [ -n "$_last_cp" ] && echo "LAST CHECKPOINT: \"$_last_cp\""
      fi
    fi
    echo ""
    echo "CONTINUE: Call trw_status() to see your current state, then resume implementation."
    echo "Your checkpoint has your progress — pick up where you left off rather than re-planning."
    ;;

  clear)
    # FR04: Minimal clear protocol
    echo "TRW: Call trw_recall('*', min_impact=0.7) to load relevant learnings from prior sessions."
    ;;

  *)
    # Fallback for unknown source
    _emit_protocol
    ;;
esac

log_hook_execution "SessionStart" "$_source" "0"

exit 0
