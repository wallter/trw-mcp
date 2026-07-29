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
# Primary cost used to be find_active_run() scanning .trw/runs/ for run.yaml
# files; since PRD-FIX-118 an identified session resolves its run with a single
# pins.json lookup and that scan is reached only when identity is unknowable.
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

# Nothing to log if no file_path
[ -n "$_file_path" ] || exit 0

# PRD-FIX-118 FR02/FR03: resolve THIS session's own run through the shared
# ownership primitive. The pins.json lookup + project-root containment check this
# hook used to inline verbatim now live once in lib-trw.sh::resolve_owned_run, so
# every hook answers "which run is mine?" the same way.
#
# The stdin payload's session_id is passed as the fallback key for the case where
# hook-env.sh predates FR01 and TRW_SESSION_ID is unset: for a client whose pin
# key IS its host session id that fallback resolves correctly, and for any other
# client it simply fails to match, which is the honest unowned outcome.
_pin_key=$(trw_pin_key "${_host_session_id:-}") || _pin_key=""
_run_dir=$(resolve_owned_run "${_host_session_id:-}") || _run_dir=""
if [ -z "$_run_dir" ]; then
  if [ -n "$_pin_key" ]; then
    # Never attribute an identified session's edit to another session's newest run.
    exit 0
  fi
  # Identity unknowable — legacy single-instance fallback (FR03 permits this
  # only on this branch).
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
