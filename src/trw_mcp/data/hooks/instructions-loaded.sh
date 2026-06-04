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
_file_path=""
_load_reason=""
_ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)" || _ts="unknown"

if command -v jq >/dev/null 2>&1; then
  _file_path=$(printf '%s' "$_payload" | jq -r '.file_path // .path // empty' 2>/dev/null) || true
  _load_reason=$(printf '%s' "$_payload" | jq -r '.load_reason // .reason // empty' 2>/dev/null) || true
else
  # grep/sed fallback — avoid unescaped user input
  _file_path=$(printf '%s' "$_payload" | grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 \
    | sed 's/.*"file_path"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
  [ -z "$_file_path" ] && _file_path=$(printf '%s' "$_payload" | grep -o '"path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 \
    | sed 's/.*"path"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
  _load_reason=$(printf '%s' "$_payload" | grep -o '"load_reason"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 \
    | sed 's/.*"load_reason"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
fi

# Ensure telemetry directory exists
_telemetry_dir="$_project_root/.trw/telemetry"
[ -d "$_telemetry_dir" ] || mkdir -p "$_telemetry_dir" 2>/dev/null || exit 0

_log_file="$_telemetry_dir/instructions-loaded.jsonl"

# Append a structured log entry — use jq when available for correct JSON escaping
if command -v jq >/dev/null 2>&1; then
  jq -n \
    --arg ts "$_ts" \
    --arg file "$_file_path" \
    --arg reason "$_load_reason" \
    '{ts: $ts, event: "instructions_loaded", file: $file, load_reason: $reason}' \
    >> "$_log_file" 2>/dev/null || true
else
  # Minimal fallback — only use fields we control (ts); skip user-supplied strings
  # to avoid JSON injection when jq is absent.
  printf '{"ts":"%s","event":"instructions_loaded","file":"(jq unavailable)","load_reason":"(jq unavailable)"}\n' \
    "$_ts" >> "$_log_file" 2>/dev/null || true
fi

# Rotate at 2000 lines to prevent unbounded growth
if [ -f "$_log_file" ]; then
  _line_count=$(wc -l < "$_log_file" 2>/dev/null | tr -d ' ') || _line_count=0
  if [ "$_line_count" -gt 2000 ] 2>/dev/null; then
    _tmp="${_log_file}.tmp"
    if tail -1000 "$_log_file" > "$_tmp" 2>/dev/null; then
      mv "$_tmp" "$_log_file" 2>/dev/null || rm -f "$_tmp" 2>/dev/null
    else
      rm -f "$_tmp" 2>/dev/null
    fi
  fi
fi

log_hook_execution "InstructionsLoaded" "$_file_path" "0"

exit 0
