#!/bin/sh
# PRD-CORE-269-FR02: advisory unfinished-work preservation.
# Missing delivery never blocks stopping or creates a stop counter/lock.
# Existing delivery detection and owner/suppression checks remain in place.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

# trw-loop worker bypass — explicit seam with trw-loop's worker runtime.
# trw-loop sets TRW_LOOP_WORKER on every worker subprocess via its worker
# runtime's subprocess-env seam. Loop workers complete via the
# MCP-unavailable receipt contract, often with TRW MCP tools absent, so a
# trw_deliver Stop reminder cannot be acted on and only wastes worker turns.
# Non-loop (interactive) sessions never set this marker, so normal advisory behavior
# is preserved. Fail-open: any error in the log call is swallowed.
if [ "${TRW_LOOP_WORKER:-}" = "1" ]; then
  log_hook_execution "Stop" "loop-worker-bypass" "0"
  exit 0
fi

# Resolve this session's identity so advice is attributed to its OWN run,
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
  exit 0
elif [ -n "$_session_id" ]; then
  # Positively unpinned: this session owns no run, so a foreign run's events must
  # not drive a block. There is no run-scoped work to enforce against here.
  exit 0
else
  # Identity unknown (no stdin/env session id) — preserve legacy single-instance
  # behavior by checking the global-newest run.
  _run_dir=$(find_active_run) || exit 0
fi
[ -n "$_run_dir" ] || exit 0

_events_path="${_run_dir}meta/events.jsonl"
[ -f "$_events_path" ] || exit 0

# Check if any events were logged
_event_count=$(wc -l < "$_events_path" 2>/dev/null | tr -d ' ') || _event_count=0
[ "$_event_count" -gt 0 ] 2>/dev/null || exit 0

# Check for ceremony completion — if present, suppress the reminder.
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
  exit 0
fi

# Advisory only: events do not prove that material work remains unfinished.
# Existing counters/locks are historical state, not a reason to block or write.
echo "TRW: If you have material unfinished work, preserve it in a checkpoint or durable native handoff with a next-read pointer. Completed-work delivery still requires its existing evidence gates." >&2

log_hook_execution "Stop" "" "0"
exit 0
