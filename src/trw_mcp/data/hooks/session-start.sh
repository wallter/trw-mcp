#!/bin/sh
# PRD-INFRA-002-FR01/FR02/FR03/FR04: Unified SessionStart hook.
# Dispatches on $SOURCE (startup|resume|compact|clear) from stdin JSON.
# Framing: value-oriented — explains what each tool gives the agent.
# Research: Anthropic context engineering, motivation framing, self-interest framing.
# Fail-open: any error silently exits 0.
#
# Performance: ~23ms avg latency (benchmarked 2026-03-29, 3 runs).
# Fires once per session event, not on every tool call.
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

_project_root="$(get_repo_root)" || exit 0

# --- PRD-CORE-060-FR06: Tier-calibrated ceremony guidance ---
_emit_tier_guidance() {
  # Find the most recent active run.yaml
  _run_yaml=""
  _task_root="$_project_root/docs"
  if [ -f "$_project_root/.trw/config.yaml" ] && command -v grep >/dev/null 2>&1; then
    _custom_root=$(grep -m1 'task_root:' "$_project_root/.trw/config.yaml" 2>/dev/null | sed 's/.*: *//' | tr -d "\"'" 2>/dev/null) || true
    [ -n "$_custom_root" ] && _task_root="$_project_root/$_custom_root"
  fi
  if [ -d "$_task_root" ]; then
    _run_yaml=$(find "$_task_root" -name "run.yaml" -path "*/meta/run.yaml" 2>/dev/null | sort -r | head -1) || true
  fi

  if [ -z "$_run_yaml" ] || [ ! -f "$_run_yaml" ]; then
    echo "CEREMONY: No active run — classify task complexity before calling trw_init."
    return
  fi

  _tier=""
  if command -v grep >/dev/null 2>&1; then
    _tier=$(grep -m1 'complexity_class:' "$_run_yaml" 2>/dev/null | sed 's/.*: *//' | tr -d "\"'" 2>/dev/null) || true
  fi

  case "$_tier" in
    MINIMAL)
      echo "CEREMONY — Tier: MINIMAL | trw_recall only | No trw_init required"
      echo "  Mandatory phases: IMPLEMENT, DELIVER"
      echo "  Skip: RESEARCH, PLAN, VALIDATE, REVIEW"
      ;;
    STANDARD)
      echo "CEREMONY — Tier: STANDARD"
      echo "  Mandatory phases: Plan, Implement, Validate, Deliver"
      echo "  1 checkpoint minimum"
      echo "  Review: optional (+10 bonus)"
      ;;
    COMPREHENSIVE)
      echo "CEREMONY — Tier: COMPREHENSIVE"
      echo "  Mandatory phases: Research, Plan, Implement, Validate, Review, Deliver"
      echo "  Multiple checkpoints, shard self-review required, adversarial audit recommended"
      ;;
    *)
      # No complexity_class or unknown — emit no tier guidance
      ;;
  esac
}

# --- Value-oriented protocol summary ---
_emit_protocol() {
  echo "## TRW Behavioral Protocol"
  echo ""
  _protocol_file="$_project_root/.trw/context/behavioral_protocol.yaml"
  if [ -f "$_protocol_file" ]; then
    grep '^ *-' "$_protocol_file" | sed 's/^ *- *//;s/^"//;s/"$//'
  else
    echo "- Start: call trw_session_start() to load prior learnings and active run state"
    echo "- During: call trw_checkpoint(message) after milestones"
    echo "- Finish: call trw_deliver() to persist learnings for future sessions"
    echo "- On errors or >2 retries: call trw_learn() to record the discovery"
  fi
}

case "$_source" in
  startup)
    # FR01: Fresh startup — explain what's available and why
    _emit_protocol
    echo ""
    _emit_tier_guidance
    echo ""
    echo "FRAMEWORK: Read .trw/frameworks/FRAMEWORK.md before starting work."
    echo "WHY: It defines the 6-phase execution model (RESEARCH → PLAN → IMPLEMENT → VALIDATE → REVIEW → DELIVER),"
    echo "  exit criteria for each phase, formation selection for parallel work, quality gates with rubric scoring,"
    echo "  phase reversion rules, and the rationalization watchlist. Your tools implement this methodology —"
    echo "  without reading it, you will pass tool checks while missing the process that prevents rework."
    echo "  The framework is ~500 lines. Read it once at session start; re-read relevant sections at phase transitions."
    echo ""
    echo "YOUR ROLE: Orchestrate, delegate, verify, and preserve knowledge."
    echo "For non-trivial tasks (2+ files), delegate to Agent Teams or subagents — focused context produces better outcomes than direct implementation."
    echo ""
    echo "RIGID (never skip): trw_session_start, trw_deliver, trw_build_check, reading FRAMEWORK.md, completion artifacts."
    echo ""
    echo "Call trw_session_start(query='your task domain') to load focused learnings and any active run state."
    ;;

  resume)
    # FR02: Resume — brief, goal-oriented
    _emit_protocol
    echo ""
    _emit_tier_guidance
    echo ""
    echo "SESSION RESUMED — your run state and learnings are preserved."
    echo "FRAMEWORK: If you haven't read .trw/frameworks/FRAMEWORK.md this session, read it now — it defines exit criteria and phase gates that govern your work."
    echo "Call trw_status() to see where you left off and what to work on next."
    ;;

  compact)
    # FR03: Compaction recovery — emphasize progress is safe, show recovered state
    echo "CONTEXT COMPACTED — your conversation was compressed but your implementation progress is safe."
    echo ""
    echo "FRAMEWORK RE-READ REQUIRED: Read .trw/frameworks/FRAMEWORK.md now, before resuming work."
    echo "WHY: Context compaction erased your understanding of the methodology. The framework itself mandates"
    echo "  re-reading after compaction (§ FRAMEWORK ADHERENCE). This costs ~500 tokens but prevents systematic"
    echo "  errors from working without phase gates, exit criteria, formation guidance, and quality rubrics."
    echo "  Agents who skip this produce work that drifts from the methodology and requires rework."
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
    echo "CONTINUE: Read .trw/frameworks/FRAMEWORK.md first, then call trw_status() to see your current state."
    echo "Your checkpoint has your progress — pick up where you left off rather than re-planning."
    ;;

  clear)
    # FR01: Clear — full protocol injection (same as startup)
    _emit_protocol
    echo ""
    echo "FRAMEWORK: Read .trw/frameworks/FRAMEWORK.md before starting work."
    echo "WHY: It defines the 6-phase execution model, exit criteria, formations, quality gates, and phase reversion"
    echo "  rules that structure your work. Your tools implement this methodology — without reading it, you will pass"
    echo "  tool checks while missing the process that prevents rework."
    echo ""
    echo "YOUR ROLE: Orchestrate, delegate, verify, and preserve knowledge."
    echo "For non-trivial tasks (2+ files), delegate to Agent Teams or subagents — focused context produces better outcomes than direct implementation."
    echo ""
    echo "Call trw_session_start(query='your task domain') to load focused learnings and any active run state."
    ;;

  *)
    # Fallback for unknown source
    _emit_protocol
    ;;
esac

log_hook_execution "SessionStart" "$_source" "0"

exit 0
