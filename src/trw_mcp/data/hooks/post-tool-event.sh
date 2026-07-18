#!/bin/sh
# PRD-INFRA-002-FR12: PostToolUse hook — auto-log file_modified events.
# Fires after Write/Edit tool completions.
#
# Reads JSON from stdin (Claude Code PostToolUse payload).
# Appends a file_modified event to the active run's events.jsonl.
#
# Exit code 0 always (fail-open, async hook).
#
# Performance: ~75ms avg latency (benchmarked 2026-03-29, 5 runs).
# Primary cost: find_active_run() scanning .trw/runs/ for run.yaml files.
# Dependencies: POSIX shell. jq optional (used for file_path extraction).

set -e
trap 'exit 0' EXIT

# Source shared utilities
_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

# PRD-CORE-149 FR05: a disabled hook must not consume stdin or append events.
if [ "${HOOKS_ENABLED:-true}" = "false" ]; then
  exit 0
fi

init_hook_timer

# Read JSON payload from stdin
_payload=$(cat) || exit 0

# Extract file_path from tool_input — jq preferred, fallback to grep
if command -v jq >/dev/null 2>&1; then
  _file_path=$(printf '%s' "$_payload" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || _file_path=""
  _tool_name=$(printf '%s' "$_payload" | jq -r '.tool_name // empty' 2>/dev/null) || _tool_name=""
  _host_session_id=$(printf '%s' "$_payload" | jq -r '.session_id // empty' 2>/dev/null) || _host_session_id=""
else
  # grep fallback: extract file_path value from JSON
  _file_path=$(printf '%s' "$_payload" | grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"file_path"[[:space:]]*:[[:space:]]*"//;s/"$//')
  _tool_name=$(printf '%s' "$_payload" | grep -o '"tool_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"tool_name"[[:space:]]*:[[:space:]]*"//;s/"$//')
  _host_session_id=$(printf '%s' "$_payload" | grep -o '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"session_id"[[:space:]]*:[[:space:]]*"//;s/"$//')
fi
_session_id=${TRW_SESSION_ID:-}
_pin_key=${_session_id:-${_host_session_id:-}}

# Nothing to log if no file_path
[ -n "$_file_path" ] || exit 0

# Resolve the acting session's durable pin before the legacy global scan.
_project_root=$(get_repo_root 2>/dev/null) || exit 0
_project_root=$(cd "$_project_root" 2>/dev/null && pwd -P) || exit 0
_task_root=$(get_task_root)
_pins_path="$_project_root/.trw/runtime/pins.json"
_run_dir=""
if [ -n "$_pin_key" ] && [ -f "$_pins_path" ]; then
  if command -v jq >/dev/null 2>&1; then
    _run_dir=$(jq -r --arg sid "$_pin_key" '.[$sid].run_path // empty' "$_pins_path" 2>/dev/null) || _run_dir=""
  elif command -v python3 >/dev/null 2>&1; then
    _run_dir=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], {}).get("run_path", ""))' "$_pins_path" "$_pin_key" 2>/dev/null) || _run_dir=""
  fi
fi
if [ -n "$_run_dir" ] && [ -d "$_run_dir" ]; then
  _run_dir=$(cd "$_run_dir" 2>/dev/null && pwd -P) || _run_dir=""
fi
case "${_run_dir%/}/" in
  "${_project_root%/}/.trw/runs/"*) _run_is_local=true ;;
  "${_project_root%/}/${_task_root%/}/"*/runs/*/) _run_is_local=true ;;
  *) _run_is_local=false ;;
esac
if [ "$_run_is_local" = true ] && [ -f "${_run_dir%/}/meta/run.yaml" ]; then
  _run_dir="${_run_dir%/}/"
elif [ -n "$_pin_key" ]; then
  # Never attribute an identified session's edit to another session's newest run.
  exit 0
else
  _run_dir=$(find_active_run) || exit 0
fi
[ -n "$_run_dir" ] || exit 0

_events_path="${_run_dir}meta/events.jsonl"

# Ensure events directory exists
[ -d "$(dirname "$_events_path")" ] || exit 0

# Append file_modified event.
# SECURITY: tool_name / file_path are attacker-influenceable — JSON-escape both
# before embedding so a value containing a quote/backslash/newline cannot break
# out of the JSON string and inject extra fields or split the JSONL line.
_tool_name_esc="$(_json_escape "$_tool_name")"
_file_path_esc="$(_json_escape "$_file_path")"
_session_id_esc="$(_json_escape "$_session_id")"
_host_session_id_esc="$(_json_escape "${_host_session_id:-}")"
append_event "$_events_path" "file_modified" "\"tool\":\"$_tool_name_esc\",\"file\":\"$_file_path_esc\",\"session_id\":\"$_session_id_esc\",\"host_session_id\":\"$_host_session_id_esc\""

log_hook_execution "PostToolUse" "$_tool_name" "0"

exit 0
