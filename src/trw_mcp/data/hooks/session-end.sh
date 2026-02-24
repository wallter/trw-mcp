#!/bin/sh
# PRD-INFRA-002-FR06: SessionEnd hook — delivery check.
# Warns (to stderr) if events were logged but trw_deliver was not called.
# Advisory only — never blocks. Fail-open pattern.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

_run_dir=$(find_active_run) || exit 0
[ -n "$_run_dir" ] || exit 0

_events_path="${_run_dir}meta/events.jsonl"
[ -f "$_events_path" ] || exit 0

# Check if any events were logged
_event_count=$(wc -l < "$_events_path" 2>/dev/null | tr -d ' ') || _event_count=0
[ "$_event_count" -gt 0 ] 2>/dev/null || exit 0

# Check for reflection event
if has_event "$_events_path" "reflection_complete" || has_event "$_events_path" "trw_reflect_complete" || has_event "$_events_path" "trw_deliver_complete"; then
  exit 0
fi

# Housekeeping: clean up per-teammate block count files
_project_root="$(git rev-parse --show-toplevel 2>/dev/null)" || true
if [ -n "$_project_root" ]; then
  cleanup_block_files "$_project_root/.trw/context"
fi

# Events exist but no reflection — warn
echo "TRW: $_event_count events logged but trw_deliver was not called. Running it captures your learnings so the next session benefits from your work." >&2

log_hook_execution "SessionEnd" "" "0"

exit 0
