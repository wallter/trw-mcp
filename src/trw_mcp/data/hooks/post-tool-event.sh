#!/bin/sh
# PRD-INFRA-002-FR12: PostToolUse hook — auto-log file_modified events.
# Fires after Write/Edit tool completions.
#
# Reads JSON from stdin (Claude Code PostToolUse payload).
# Appends a file_modified event to the active run's events.jsonl.
#
# Exit code 0 always (fail-open, async hook).
# Dependencies: POSIX shell. jq optional (used for file_path extraction).

set -e
trap 'exit 0' EXIT

# Source shared utilities
_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

# Read JSON payload from stdin
_payload=$(cat) || exit 0

# Extract file_path from tool_input — jq preferred, fallback to grep
if command -v jq >/dev/null 2>&1; then
  _file_path=$(printf '%s' "$_payload" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || _file_path=""
  _tool_name=$(printf '%s' "$_payload" | jq -r '.tool_name // empty' 2>/dev/null) || _tool_name=""
else
  # grep fallback: extract file_path value from JSON
  _file_path=$(printf '%s' "$_payload" | grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"file_path"[[:space:]]*:[[:space:]]*"//;s/"$//')
  _tool_name=$(printf '%s' "$_payload" | grep -o '"tool_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"tool_name"[[:space:]]*:[[:space:]]*"//;s/"$//')
fi

# Nothing to log if no file_path
[ -n "$_file_path" ] || exit 0

# Find active run — early exit if none (~10ms)
_run_dir=$(find_active_run) || exit 0
[ -n "$_run_dir" ] || exit 0

_events_path="${_run_dir}meta/events.jsonl"

# Ensure events directory exists
[ -d "$(dirname "$_events_path")" ] || exit 0

# Append file_modified event
append_event "$_events_path" "file_modified" "\"tool\":\"$_tool_name\",\"file\":\"$_file_path\""

log_hook_execution "PostToolUse" "$_tool_name" "0"

exit 0
