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

# Housekeeping is SESSION-scoped, not run-scoped, so it runs on every session end
# regardless of which run (if any) this session owns. It used to sit AFTER the
# run-scoped early exits, which meant it only ever ran in the narrow "this run has
# events and no reflection" case — and once the run is resolved by ownership
# instead of recency, an unowned session would have stopped cleaning up entirely.
_project_root="$(get_repo_root)" || true
if [ -n "$_project_root" ]; then
  cleanup_block_files "$_project_root/.trw/context"
  cleanup_phase_cycle "$_project_root"
fi

# --- Resolve THIS SESSION'S OWN run (PRD-FIX-118 FR03) ---------------------
# The warning below quotes a concrete event count as "your work". Sourced by
# recency it reports a parallel instance's events, which is both untrue and
# unactionable — the reader has no access to that run.
#
# What "unowned" means HERE: skip the run-scoped warning. The premise ("N events
# were logged into your run") is unverifiable without an owned run, and there is
# no session-scoped event count to substitute — .trw/context/session-events.jsonl
# is a shared append-only log, so a run-less variant would fire on EVERY session
# end whether or not this session did anything. The delivery reminder that
# actually gates is stop-ceremony.sh, which handles the unpinned case explicitly;
# this hook is the advisory echo of it. Housekeeping above still runs.
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
_session_id=$(trw_pin_key "$_session_id" 2>/dev/null) || _session_id=""

# PRD-FIX-128-FR08: reclaim THIS session's degraded markers. Deliberately placed
# BEFORE the run-scoped early exits below -- a session that ended without owning
# a run still has an epoch marker and possibly a latch, and leaving them to the
# age-based sweep would keep a dead session's epoch around for a day. The
# `command -v` guard matches the ones already used for this subsystem: a
# .claude/hooks copy that predates FR128 has no such function.
if command -v trw_degraded_release_markers >/dev/null 2>&1; then
  trw_degraded_release_markers "$_session_id"
fi

_run_dir=""
if [ -n "$_session_id" ]; then
  _run_dir=$(resolve_owned_run "$_session_id" 2>/dev/null) || _run_dir=""
  if [ -z "$_run_dir" ]; then
    log_hook_execution "SessionEnd" "unowned" "0"
    exit 0
  fi
else
  # Identity unknown — legacy newest-wins keeps single-instance clients warned.
  _run_dir=$(find_active_run) || exit 0
fi
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

# Events exist but no reflection — warn
echo "TRW: $_event_count events logged but trw_deliver was not called. Running it captures your learnings so the next session benefits from your work." >&2

log_hook_execution "SessionEnd" "" "0"

exit 0
