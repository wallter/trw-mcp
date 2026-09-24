#!/bin/sh
# PostCompact hook — TRW recovery context injection.
# Fires immediately after context compaction completes.
# Emits recovery context so Claude can resume without waiting
# for the next user prompt (which is when SessionStart compact-matcher fires).
# Complements PreCompact (which snapshots state) and SessionStart (compact branch).
# Fail-open: any error silently exits 0. Never blocks.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

_project_root="$(get_repo_root)" || exit 0

echo "## TRW POST-COMPACTION RECOVERY"
echo ""
echo "Context compaction completed. Your implementation progress is preserved."
echo "This recovery context is injected automatically by the PostCompact hook."
echo ""

# Recover THIS session's pre-compaction marker (written by pre-compact.sh)
_state_file=$(pre_compact_state_file "$_project_root" 2>/dev/null) || _state_file=""
_run_path=""
_phase=""
_event_count=0
_last_cp=""

if [ -f "$_state_file" ] && _trw_has_json_parser; then
  _run_path=$(_json_get --file "$_state_file" .run_path) || true
  _phase=$(_json_get --file "$_state_file" .phase) || true
  _event_count=$(_json_get --file "$_state_file" --default 0 .events_logged) || true
  _last_cp=$(_json_get --file "$_state_file" .last_checkpoint) || true
fi

if [ -n "$_run_path" ]; then
  echo "RECOVERED RUN: $_run_path"
  [ -n "$_phase" ] && echo "RECOVERED PHASE: $_phase | Events logged: ${_event_count:-0}"
  [ -n "$_last_cp" ] && echo "LAST CHECKPOINT: \"$_last_cp\""
  echo ""
  echo "NEXT STEPS:"
  echo "  1. Read your phase's sections of .trw/frameworks/FRAMEWORK.md (the SessionStart reload names them)"
  echo "  2. Call trw_session_start(query='your task domain') to reload learnings"
  echo "  3. Call trw_status() to confirm current phase"
  echo "  4. Resume from the last checkpoint — do not re-plan"
else
  echo "No active run found in pre-compaction snapshot."
  echo "Call trw_session_start() to check for any active run and reload learnings."
fi

echo ""
echo "MANDATORY: Read your phase's sections of .trw/frameworks/FRAMEWORK.md before resuming work."
echo "WHY: Compaction erased your understanding of the 6-phase protocol, exit criteria,"
echo "  and quality gates. Skipping this produces methodology drift and rework."

log_hook_execution "PostCompact" "" "0"

exit 0
