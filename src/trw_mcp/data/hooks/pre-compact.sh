#!/bin/sh
# PRD-INFRA-002-FR05: PreCompact hook — state snapshot.
# Saves active run state to .trw/context/pre_compact_state.json
# before context compaction so it can be recovered afterwards.
# Fail-open: any error silently exits 0.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

_project_root="$(get_repo_root)" || exit 0
_context_dir="$_project_root/.trw/context"
[ -d "$_context_dir" ] || mkdir -p "$_context_dir" 2>/dev/null || exit 0
_injected_file="$_context_dir/injected_learning_ids.txt"

# PRD-CORE-095 FR17: clear injected-learning dedup state before compaction.
: > "$_injected_file" 2>/dev/null || true

# Determine trigger type from stdin
_payload=$(cat) || exit 0
_trigger="unknown"
if command -v jq >/dev/null 2>&1; then
  _trigger=$(printf '%s' "$_payload" | jq -r '.source // "unknown"' 2>/dev/null) || true
fi

_ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)" || _ts="unknown"

# Find active run
_run_dir=$(find_active_run) || true

_run_path=""
_phase=""
_event_count=0
_last_checkpoint=""
_wave_manifest=""
_active_tasks=0
_pending_decisions=""

if [ -n "$_run_dir" ]; then
  _run_path="$_run_dir"

  # Extract phase from run.yaml
  _run_yaml="${_run_dir}meta/run.yaml"
  if [ -f "$_run_yaml" ]; then
    _phase=$(grep '^phase:' "$_run_yaml" | head -1 | sed 's/^phase:[[:space:]]*//' | tr -d "'" | tr -d '"') || true
  fi

  # Count events
  _events_path="${_run_dir}meta/events.jsonl"
  if [ -f "$_events_path" ]; then
    _event_count=$(wc -l < "$_events_path" 2>/dev/null | tr -d ' ') || _event_count=0
  fi

  # Get last checkpoint message
  _cp_path="${_run_dir}meta/checkpoints.jsonl"
  if [ -f "$_cp_path" ] && command -v jq >/dev/null 2>&1; then
    _last_checkpoint=$(tail -1 "$_cp_path" 2>/dev/null | jq -r '.message // empty' 2>/dev/null) || true
  fi

  # FR02: wave_manifest — read wave status from wave_manifest.yaml or run.yaml
  _wave_yaml="${_run_dir}meta/wave_manifest.yaml"
  if [ -f "$_wave_yaml" ]; then
    _wave_manifest=$(grep '^status:' "$_wave_yaml" | head -1 | sed 's/^status:[[:space:]]*//' | tr -d "'" | tr -d '"') || true
    [ -z "$_wave_manifest" ] && _wave_manifest="present"
  elif [ -f "$_run_yaml" ]; then
    _wave_manifest=$(grep '^wave:' "$_run_yaml" | head -1 | sed 's/^wave:[[:space:]]*//' | tr -d "'" | tr -d '"') || true
  fi

  # FR02: active_tasks — count in-progress tasks from task directory
  _task_dir="${_run_dir}tasks"
  if [ -d "$_task_dir" ]; then
    _active_tasks=$(grep -rl '"status"[[:space:]]*:[[:space:]]*"in_progress"\|^status:[[:space:]]*in_progress' "$_task_dir" 2>/dev/null | wc -l | tr -d ' ') || _active_tasks=0
  fi

  # FR02: pending_decisions — open questions from last checkpoint
  if [ -f "$_cp_path" ] && command -v jq >/dev/null 2>&1; then
    _pending_decisions=$(tail -1 "$_cp_path" 2>/dev/null | jq -r '.pending_decisions // .open_questions // empty' 2>/dev/null) || true
  fi
fi

# Write state snapshot
_state_file="$_context_dir/pre_compact_state.json"
if command -v jq >/dev/null 2>&1; then
  jq -n \
    --arg ts "$_ts" \
    --arg trigger "$_trigger" \
    --arg run_path "$_run_path" \
    --arg phase "$_phase" \
    --argjson event_count "${_event_count:-0}" \
    --arg last_checkpoint "$_last_checkpoint" \
    --arg wave_manifest "$_wave_manifest" \
    --argjson active_tasks "${_active_tasks:-0}" \
    --arg pending_decisions "$_pending_decisions" \
    '{ts: $ts, trigger: $trigger, run_path: $run_path, phase: $phase, events_logged: $event_count, last_checkpoint: $last_checkpoint, wave_manifest: $wave_manifest, active_tasks: $active_tasks, pending_decisions: $pending_decisions}' \
    > "$_state_file" 2>/dev/null
else
  # Fallback: minimal JSON (no user-controlled strings to avoid injection)
  printf '{"ts":"%s","trigger":"%s","run_path":"%s","phase":"%s","events_logged":%s}\n' \
    "$_ts" "$_trigger" "$_run_path" "$_phase" "${_event_count:-0}" \
    > "$_state_file" 2>/dev/null
fi

log_hook_execution "PreCompact" "$_trigger" "0"

exit 0
