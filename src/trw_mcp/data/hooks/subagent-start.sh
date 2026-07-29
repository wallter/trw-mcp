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

# --- Resolve THIS SESSION'S OWN run (PRD-FIX-118 FR03) ---------------------
# Everything this hook prints is injected verbatim into a fresh subagent's
# context. Resolved by recency it hands the subagent a parallel instance's run
# path and phase — and the phase then selects which checklist to inject, so the
# subagent is coached for a phase it is not in and may write into a foreign run.
# A subagent shell inherits its parent's session id, so a subagent of a pinned
# session resolves the SAME run.
#
# What "unowned" means HERE: print the protocol reminders (recall / learn /
# checkpoint) and omit ONLY the run-derived lines. Those reminders are not
# run-scoped and are the hook's actual reason to exist, so the injection keeps
# working; it just stops asserting foreign state (FR04).
_ss_session_id=""
if command -v jq >/dev/null 2>&1; then
  _ss_session_id=$(printf '%s' "$_payload" | jq -r '.session_id // empty' 2>/dev/null) || true
fi
if [ -z "$_ss_session_id" ]; then
  _ss_session_id=$(printf '%s' "$_payload" | grep -o '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"session_id"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
fi
_ss_session_id=$(trw_pin_key "$_ss_session_id" 2>/dev/null) || _ss_session_id=""

_run_dir=""
if [ -n "$_ss_session_id" ]; then
  _run_dir=$(resolve_owned_run "$_ss_session_id" 2>/dev/null) || _run_dir=""
else
  # Identity unknown — legacy newest-wins for single-instance clients.
  _run_dir=$(find_active_run) || _run_dir=""
fi
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
    echo "Integration is part of done: new code must be imported and called from existing code."
    echo "Record validation with trw_build_check(tests_passed, test_count, failure_count, static_checks_clean, scope), then trw_checkpoint your result — FRs implemented, tests, integration points."
    ;;
  validate*)
    echo ""
    echo "VALIDATE PHASE: Run project-native checks, then record observed results with trw_build_check(tests_passed, test_count, failure_count, static_checks_clean, scope). Verify the configured coverage target and P0 status."
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
