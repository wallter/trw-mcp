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

_project_root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
_context_dir="$_project_root/.trw/context"
[ -d "$_context_dir" ] || mkdir -p "$_context_dir" 2>/dev/null || exit 0

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
    '{timestamp: $ts, trigger: $trigger, run_path: $run_path, phase: $phase, events_logged: $event_count, last_checkpoint: $last_checkpoint}' \
    > "$_state_file" 2>/dev/null
else
  # Fallback: manual JSON construction
  printf '{"timestamp":"%s","trigger":"%s","run_path":"%s","phase":"%s","events_logged":%s,"last_checkpoint":"%s"}\n' \
    "$_ts" "$_trigger" "$_run_path" "$_phase" "${_event_count:-0}" "$_last_checkpoint" \
    > "$_state_file" 2>/dev/null
fi

log_hook_execution "PreCompact" "$_trigger" "0"

exit 0
