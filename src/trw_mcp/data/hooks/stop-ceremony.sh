#!/bin/sh
# PRD-INFRA-002-FR13: Stop hook — ceremony enforcement.
# Blocks (exit 2) if events logged but no reflection, max 2 blocks.
# After 2 blocks, warns but allows exit. Fail-open on errors.
# Uses mkdir as atomic lock to prevent race conditions from concurrent
# Stop events (Claude Code can fire multiple Stop events in rapid succession).
set -e
_trw_intentional_exit=""
trap '[ -n "$_trw_intentional_exit" ] || exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

_project_root="$(get_repo_root)" || exit 0
_context_dir="$_project_root/.trw/context"
_block_file="$_context_dir/stop_block_count"
_lock_dir="$_context_dir/stop_hook.lock"

_run_dir=$(find_active_run) || exit 0
[ -n "$_run_dir" ] || exit 0

_events_path="${_run_dir}meta/events.jsonl"
[ -f "$_events_path" ] || exit 0

# Check if any events were logged
_event_count=$(wc -l < "$_events_path" 2>/dev/null | tr -d ' ') || _event_count=0
[ "$_event_count" -gt 0 ] 2>/dev/null || exit 0

# Check for ceremony completion — if present, clear block count and allow
if has_event "$_events_path" "reflection_complete" || has_event "$_events_path" "trw_reflect_complete" || has_event "$_events_path" "trw_deliver_complete"; then
  rm -f "$_block_file" 2>/dev/null || true
  rm -rf "$_lock_dir" 2>/dev/null || true
  exit 0
fi

# Acquire lock (mkdir is atomic on POSIX). Fail-open if lock held.
[ -d "$_context_dir" ] || mkdir -p "$_context_dir" 2>/dev/null || exit 0
if ! mkdir "$_lock_dir" 2>/dev/null; then
  # Another Stop hook is running concurrently — allow this one through
  exit 0
fi
# Ensure lock is released on exit
trap 'rm -rf "$_lock_dir" 2>/dev/null; [ -n "$_trw_intentional_exit" ] || exit 0' EXIT

# Read block count (under lock, so no races)
_blocks=0
if [ -f "$_block_file" ]; then
  _blocks=$(tr -d '[:space:]' < "$_block_file" 2>/dev/null) || true
fi
_blocks=$((${_blocks:-0} + 0)) 2>/dev/null || _blocks=0

if [ "$_blocks" -ge 2 ]; then
  # Max blocks reached — warn but allow
  echo "TRW: $_event_count events from this session. Running trw_deliver() next session captures your learnings. Allowing exit after 2 reminders." >&2
  rm -f "$_block_file" 2>/dev/null || true
  log_hook_execution "Stop" "" "0"
  exit 0
fi

# Block: increment counter and exit 2
_blocks=$((_blocks + 1))
printf '%s' "$_blocks" > "$_block_file" 2>/dev/null || true
echo "TRW: trw_deliver() has not been called yet ($_event_count events logged). Running it now preserves your learnings and progress for future sessions. (Reminder $_blocks/2)" >&2

log_hook_execution "Stop" "" "2"

_trw_intentional_exit=1
exit 2
