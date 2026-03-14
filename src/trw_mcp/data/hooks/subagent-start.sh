#!/bin/sh
# PRD-INFRA-002-FR07: SubagentStart hook — TRW context injection.
# Injects abbreviated TRW protocol + active run context into subagents.
# Includes phase-specific guidance so shards know what's expected.
# Fail-open: any error silently exits 0.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

# Read stdin payload (needed for telemetry agent_type extraction)
_payload=$(cat) || exit 0

_project_root="$(get_repo_root)" || true
_run_dir=$(find_active_run) || true
_phase=""

echo "TRW SUB-AGENT CONTEXT:"
echo "- Call trw_recall(query='your domain') for relevant prior learnings before starting work"
echo "- Call trw_learn to record discoveries and gotchas for future sessions"
echo "- Call trw_checkpoint after each milestone with a summary of what you completed"

if [ -n "$_run_dir" ]; then
  echo "- Active run: $_run_dir"
  _run_yaml="${_run_dir}meta/run.yaml"
  if [ -f "$_run_yaml" ]; then
    _phase=$(grep '^phase:' "$_run_yaml" | head -1 | sed 's/^phase:[[:space:]]*//' | tr -d "'" | tr -d '"') || true
    [ -n "$_phase" ] && echo "- Current phase: $_phase"
  fi
fi

# Phase-specific guidance — agents skip VALIDATE→REVIEW without explicit reminders
case "$_phase" in
  implement*)
    echo ""
    echo "BEFORE COMPLETING YOUR WORK — self-review checklist:"
    echo "  1. Re-read your assigned PRD FRs — verify EVERY requirement is implemented"
    echo "  2. Check integration — new code must be imported and called from existing code"
    echo "  3. Review your diff for DRY/KISS/SOLID quality"
    echo "  4. Run trw_build_check(scope='full') to confirm pytest + mypy pass"
    echo "  5. Write a completion summary in trw_checkpoint: FRs implemented, tests, integration points"
    echo "Doing this self-review now saves the project a full rework pass later."
    ;;
  validate*)
    echo ""
    echo "VALIDATE PHASE: Run trw_build_check(scope='full'). Verify coverage >= target. Check for P0 findings."
    ;;
  review*)
    echo ""
    echo "REVIEW PHASE: Review the diff for quality (DRY/KISS/SOLID). Fix incomplete integrations. Record learnings."
    ;;
esac

# FR06: Telemetry event for paired start/stop analysis
_log_file="${_project_root:+$_project_root/.trw/logs/subagent-events.jsonl}"
if [ -n "$_log_file" ] && [ -d "$(dirname "$_log_file")" ]; then
  _ts_telem="$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)" || _ts_telem="unknown"
  if command -v jq >/dev/null 2>&1; then
    _agent_type_telem=$(printf '%s' "$_payload" | jq -r '.agent_type // .subagent_type // "unknown"' 2>/dev/null) || _agent_type_telem="unknown"
    jq -n --arg ts "$_ts_telem" --arg event "subagent_start" --arg agent_type "$_agent_type_telem" \
      '{ts: $ts, event: $event, agent_type: $agent_type}' >> "$_log_file" 2>/dev/null
  else
    printf '{"ts":"%s","event":"subagent_start","agent_type":"unknown"}\n' \
      "$_ts_telem" >> "$_log_file" 2>/dev/null
  fi
fi

log_hook_execution "SubagentStart" "" "0"

exit 0
