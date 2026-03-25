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
      echo "CEREMONY — Tier: MINIMAL | Trivial 1-file fix only"
      echo "  Mandatory phases: IMPLEMENT, VALIDATE, DELIVER"
      echo "  Skip: RESEARCH, PLAN, REVIEW"
      ;;
    STANDARD)
      echo "CEREMONY — Tier: STANDARD"
      echo "  Mandatory phases: Plan, Implement, Validate, Review, Deliver"
      echo "  1 checkpoint minimum, review required before delivery"
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
    echo "- Start: call trw_session_start() — loads prior learnings so you start from accumulated knowledge, not zero"
    echo "- During: call trw_checkpoint(message) after milestones — your last checkpoint is your resume point if context compacts"
    echo "- Finish: call trw_deliver() — without it, your session's discoveries are invisible to every future agent"
    echo "- On errors or >2 retries: call trw_learn() — saves the gotcha so no future agent repeats your mistake"
  fi
}

case "$_source" in
  startup)
    # FR01: Fresh startup — full operational briefing
    _emit_protocol
    echo ""
    _emit_tier_guidance
    echo ""
    echo "FRAMEWORK: Read .trw/frameworks/FRAMEWORK.md before starting work."
    echo "WHY: It defines the 6-phase execution model, exit criteria, formations, quality gates, and phase reversion"
    echo "  rules that structure your work. Your tools implement this methodology — without reading it, you will pass"
    echo "  tool checks while missing the process that prevents rework."
    echo ""
    echo "YOUR ROLE: Orchestrate, delegate, verify, and preserve knowledge."
    echo "For non-trivial tasks (2+ files), delegate to Agent Teams or subagents — focused context produces better outcomes than direct implementation."
    echo ""
    echo "## Delegation Decision Tree"
    echo "  Task arrives → Assess scope"
    echo "  ├── Trivial? (≤3 lines, 1 file) → Self-implement"
    echo "  ├── Research/read-only?          → Subagent (Explore/Plan type)"
    echo "  ├── Single-scope? (≤3 files)     → Subagent (general-purpose)"
    echo "  ├── Multi-scope? (4+ files)"
    echo "  │   ├── Independent tracks?      → Batched subagents"
    echo "  │   └── Interdependent?          → Agent Team"
    echo "  └── Sprint-scale? (4+ PRDs)      → Agent Team + playbooks"
    echo ""
    echo "## Rationalization Watchlist"
    echo "If you catch yourself thinking any of these, stop and follow the process:"
    echo "  'This is too simple for ceremony' → Simple tasks compound into gaps when 10 agents skip in parallel"
    echo "  'I'll checkpoint/deliver after I finish' → Context compaction erases uncheckpointed work permanently"
    echo "  'I already know the codebase' → Prior learnings contain gotchas for exactly this area"
    echo "  'I can implement directly' → Subagent implementation has 3x fewer P0 defects"
    echo "  'The build check can wait' → Late build failures cascade into multi-file rework"
    echo ""
    echo "RIGID (never skip): trw_session_start, trw_deliver, trw_build_check, reading FRAMEWORK.md, completion artifacts."
    echo "WHY: These are the tools where skipping costs more than running — lost learnings, shipped bugs, false completions."
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
    echo "## Delegation Decision Tree"
    echo "  Task arrives → Assess scope"
    echo "  ├── Trivial? (≤3 lines, 1 file) → Self-implement"
    echo "  ├── Research/read-only?          → Subagent (Explore/Plan type)"
    echo "  ├── Single-scope? (≤3 files)     → Subagent (general-purpose)"
    echo "  ├── Multi-scope? (4+ files)"
    echo "  │   ├── Independent tracks?      → Batched subagents"
    echo "  │   └── Interdependent?          → Agent Team"
    echo "  └── Sprint-scale? (4+ PRDs)      → Agent Team + playbooks"
    echo ""
    echo "## Rationalization Watchlist"
    echo "If you catch yourself thinking any of these, stop and follow the process:"
    echo "  'This is too simple for ceremony' → Simple tasks compound into gaps when 10 agents skip in parallel"
    echo "  'I'll checkpoint/deliver after I finish' → Context compaction erases uncheckpointed work permanently"
    echo "  'I already know the codebase' → Prior learnings contain gotchas for exactly this area"
    echo "  'I can implement directly' → Subagent implementation has 3x fewer P0 defects"
    echo "  'The build check can wait' → Late build failures cascade into multi-file rework"
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
