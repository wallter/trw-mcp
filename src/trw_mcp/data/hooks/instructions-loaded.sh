#!/bin/sh
# InstructionsLoaded hook — observability audit trail for loaded rule files.
# Logs which CLAUDE.md or .claude/rules/*.md file loaded, when, and why.
# Zero ceremony cost: never blocks, never exits non-zero.
# Provides ground-truth for debugging path-scoped rule failures.
# Fail-open: any error silently exits 0.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

_project_root="$(get_repo_root)" || exit 0

# Read stdin payload
_payload=$(cat) || exit 0

# Extract fields from InstructionsLoaded payload
_ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)" || _ts="unknown"

# PRD-FIX-154 FR03: jq or python3 via _json_get, never a jq-only read -- a
# jq-less host with python3 now records the real file/reason instead of
# "(jq unavailable)".
_file_path=$(printf '%s' "$_payload" | _json_get --strings .file_path .path) || _file_path=""
_load_reason=$(printf '%s' "$_payload" | _json_get --strings .load_reason .reason) || _load_reason=""

# Ensure telemetry directory exists
_telemetry_dir="$_project_root/.trw/telemetry"
[ -d "$_telemetry_dir" ] || mkdir -p "$_telemetry_dir" 2>/dev/null || exit 0

_log_file="$_telemetry_dir/instructions-loaded.jsonl"

# Append a structured log entry -- _json_object escapes correctly on either path.
_json_object --str ts "$_ts" --str event "instructions_loaded" --str file "$_file_path" --str load_reason "$_load_reason" \
  | _trw_safe_write "$_log_file" append || true

# Rotate at 2000 lines to prevent unbounded growth. _trw_safe_read treats a
# symlinked log as absent, so rotation no-ops on one instead of following it.
_il_content=$(_trw_safe_read "$_log_file") || _il_content=""
if [ -n "$_il_content" ]; then
  _line_count=$(printf '%s\n' "$_il_content" | wc -l 2>/dev/null | tr -d ' ') || _line_count=0
  if [ "$_line_count" -gt 2000 ] 2>/dev/null; then
    printf '%s\n' "$_il_content" | tail -1000 | _trw_safe_write "$_log_file" || true
  fi
fi

log_hook_execution "InstructionsLoaded" "$_file_path" "0"

exit 0
