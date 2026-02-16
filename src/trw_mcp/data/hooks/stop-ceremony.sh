#!/bin/sh
# PRD-INFRA-002-FR13: Stop hook — ceremony enforcement.
# Blocks (exit 2) if events logged but no reflection, max 2 blocks.
# After 2 blocks, warns but allows exit. Fail-open on errors.
set -e
_trw_intentional_exit=""
trap '[ -n "$_trw_intentional_exit" ] || exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

_project_root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
_block_file="$_project_root/.trw/context/stop_block_count"
_context_dir="$_project_root/.trw/context"

_run_dir=$(find_active_run "docs") || exit 0
[ -n "$_run_dir" ] || exit 0

_events_path="${_run_dir}meta/events.jsonl"
[ -f "$_events_path" ] || exit 0

# Check if any events were logged
_event_count=$(wc -l < "$_events_path" 2>/dev/null | tr -d ' ') || _event_count=0
[ "$_event_count" -gt 0 ] 2>/dev/null || exit 0

# Check for reflection event — if present, clear block count and exit
if has_event "$_events_path" "reflection_complete" || has_event "$_events_path" "trw_reflect_complete"; then
  rm -f "$_block_file" 2>/dev/null || true
  exit 0
fi

# No reflection found — check block count
[ -d "$_context_dir" ] || mkdir -p "$_context_dir" 2>/dev/null || exit 0
_blocks=0
if [ -f "$_block_file" ]; then
  _blocks=$(tr -d '[:space:]' < "$_block_file" 2>/dev/null) || true
fi
_blocks=$((${_blocks:-0} + 0)) 2>/dev/null || _blocks=0

if [ "$_blocks" -ge 2 ]; then
  # Max blocks reached — warn but allow
  echo "TRW WARNING: Ceremony incomplete (trw_reflect not called, $_event_count events logged). Allowing exit after 2 previous blocks." >&2
  rm -f "$_block_file" 2>/dev/null || true
  exit 0
fi

# Block: increment counter and exit 2
_blocks=$((_blocks + 1))
printf '%s' "$_blocks" > "$_block_file" 2>/dev/null || true
echo "TRW BLOCK: Execute trw_deliver() or trw_reflect() before stopping. $_event_count events logged but no reflection found. (Block $_blocks/2)" >&2

log_hook_execution "Stop" "" "2"

_trw_intentional_exit=1
exit 2
