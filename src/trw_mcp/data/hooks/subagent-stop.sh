#!/bin/sh
# PRD-INFRA-038-FR05: SubagentStop hook — telemetry event logging.
# Emits structured JSONL to .trw/logs/subagent-events.jsonl for lifecycle tracking.
# Fail-open: any error silently exits 0.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

_project_root="$(get_repo_root)" || exit 0
_log_dir="$_project_root/.trw/logs"
[ -d "$_log_dir" ] || mkdir -p "$_log_dir" 2>/dev/null || exit 0

# Read stdin payload
_payload=$(cat) || exit 0
_agent_type=""
if command -v jq >/dev/null 2>&1; then
  _agent_type=$(printf '%s' "$_payload" | jq -r '.agent_type // .subagent_type // "unknown"' 2>/dev/null) || true
fi

_ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)" || _ts="unknown"

# Write telemetry event (key: "ts" matches lib-trw.sh append_event convention)
_log_file="$_log_dir/subagent-events.jsonl"
if command -v jq >/dev/null 2>&1; then
  jq -n \
    --arg ts "$_ts" \
    --arg event "subagent_stop" \
    --arg agent_type "$_agent_type" \
    '{ts: $ts, event: $event, agent_type: $agent_type}' \
    >> "$_log_file" 2>/dev/null
else
  printf '{"ts":"%s","event":"subagent_stop","agent_type":"unknown"}\n' \
    "$_ts" >> "$_log_file" 2>/dev/null
fi

log_hook_execution "SubagentStop" "$_agent_type" "0"
exit 0
