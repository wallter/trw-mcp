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

# trw-loop worker bypass — explicit seam with trw-loop's worker runtime.
# trw-loop sets TRW_LOOP_WORKER on every worker subprocess via its worker
# runtime's subprocess-env seam. Loop workers complete via the
# MCP-unavailable receipt contract, often with TRW MCP tools absent, so a
# trw_deliver Stop reminder cannot be acted on and only wastes worker turns.
# Non-loop (interactive) sessions never set this marker, so normal enforcement
# is preserved. Fail-open: any error in the log call is swallowed.
if [ "${TRW_LOOP_WORKER:-}" = "1" ]; then
  log_hook_execution "Stop" "loop-worker-bypass" "0"
  exit 0
fi

# Resolve this session's identity so enforcement is attributed to its OWN run,
# not a parallel instance's newest run. TRW_SESSION_ID wins; otherwise parse the
# session_id out of the Stop-hook stdin JSON. Must consume stdin before any other
# stdin-reading command.
_session_id="${TRW_SESSION_ID:-}"
if [ -z "$_session_id" ] && ! [ -t 0 ]; then
  _stdin_payload=$(cat 2>/dev/null) || _stdin_payload=""
  if [ -n "$_stdin_payload" ]; then
    _session_id=$(printf '%s' "$_stdin_payload" \
      | grep -o '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' \
      | head -1 \
      | sed 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/') || _session_id=""
  fi
fi

_project_root="$(get_repo_root)" || exit 0
_context_dir="$_project_root/.trw/context"
_block_file="$_context_dir/stop_block_count"
_lock_dir="$_context_dir/stop_hook.lock"

# FOREIGN-run hardening (PRD ceremony-nudge false-positive fix): attribute
# enforcement to THIS session's own pinned run when resolvable. An unpinned
# session must never be nagged based on a parallel instance's newest run.
#   - own pin resolvable   -> enforce against that run (correct attribution)
#   - no own pin + recent session-scoped deliver -> clear (unpinned deliver marker)
#   - no own pin + known unpinned identity        -> nothing run-scoped to enforce
#   - identity unknown                             -> legacy global-newest fallback
# The session-scoped check is two-shaped on purpose (PRD-FIX-117 FR01): an
# unpinned delivery lands in session-events.jsonl as
# {"event":"tool_invocation","tool_name":"trw_deliver","success":true}, never as
# the trw_deliver_complete type the first predicate greps for.
# PRD-FIX-117 FR02: consume PRD-FIX-118's single run-ownership primitive rather
# than reading the pin store directly. resolve_owned_run adds the project-root
# CONTAINMENT check this site lacked (a pin whose run_path escapes the project is
# rejected) and routes through trw_pin_key, so TRW_SESSION_ID wins over the
# stdin-parsed id. Ownership is never established by recency.
_own_run_dir=""
if [ -n "$_session_id" ]; then
  _own_run_dir=$(resolve_owned_run "$_session_id" 2>/dev/null) || _own_run_dir=""
fi

if [ -n "$_own_run_dir" ] && [ -d "$_own_run_dir" ]; then
  case "$_own_run_dir" in
    */) _run_dir="$_own_run_dir" ;;
    *) _run_dir="$_own_run_dir/" ;;
  esac
elif has_recent_session_deliver 240 || has_recent_session_tool_deliver; then
  # Unpinned (or unresolved-pin) session with a recent session-scoped deliver —
  # its completion marker lands only in session-events. Trust it and clear.
  rm -f "$_block_file" 2>/dev/null || true
  rm -rf "$_lock_dir" 2>/dev/null || true
  exit 0
elif [ -n "$_session_id" ]; then
  # Positively unpinned: this session owns no run, so a foreign run's events must
  # not drive a block. There is no run-scoped work to enforce against here.
  exit 0
else
  # Identity unknown (no stdin/env session id) — preserve legacy single-instance
  # behavior by enforcing against the global-newest run.
  _run_dir=$(find_active_run) || exit 0
fi
[ -n "$_run_dir" ] || exit 0

_events_path="${_run_dir}meta/events.jsonl"
[ -f "$_events_path" ] || exit 0

# Check if any events were logged
_event_count=$(wc -l < "$_events_path" 2>/dev/null | tr -d ' ') || _event_count=0
[ "$_event_count" -gt 0 ] 2>/dev/null || exit 0

# Check for ceremony completion — if present, clear block count and allow.
# Sources, in order: (1) this run's own events, (2) a RECENT session-scoped
# deliver marker (recency-bounded so a persisted marker cannot clear every
# future session), (3) a RECENT successful trw_deliver TOOL INVOCATION in the
# session log, (4) any parallel instance's recent run.
_deliver_found=false
if has_event "$_events_path" "reflection_complete" || has_event "$_events_path" "trw_reflect_complete" || has_event "$_events_path" "trw_deliver_complete"; then
  _deliver_found=true
elif has_recent_session_deliver 240; then
  _deliver_found=true
elif has_recent_session_tool_deliver; then
  # PRD-FIX-117 FR01. The branch above greps session-events.jsonl for the event
  # type "trw_deliver_complete", which the unpinned write path never emits: it
  # writes {"event":"tool_invocation","tool_name":"trw_deliver","success":true}.
  # Match the shape the writer actually emits, bounded by the row's own ts.
  _deliver_found=true
elif has_recent_deliver 240; then
  # Another parallel instance delivered recently — don't block this one
  _deliver_found=true
fi
if [ "$_deliver_found" = true ]; then
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
