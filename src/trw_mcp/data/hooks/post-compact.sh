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

# PRD-CORE-125-FR05: Respect hooks-disabled gate.
if [ "${TRW_HOOKS_ENABLED:-true}" = "false" ]; then
  exit 0
fi

echo "## TRW POST-COMPACTION RECOVERY"
echo ""
echo "Context compaction completed. Your implementation progress is preserved."
echo "This recovery context is injected automatically by the PostCompact hook."
echo ""

# Recover state from pre_compact_state.json (written by pre-compact.sh)
_state_file="$_project_root/.trw/context/pre_compact_state.json"
_run_path=""
_phase=""
_event_count=0
_last_cp=""

if [ -f "$_state_file" ] && command -v jq >/dev/null 2>&1; then
  _run_path=$(jq -r '.run_path // empty' "$_state_file" 2>/dev/null) || true
  _phase=$(jq -r '.phase // empty' "$_state_file" 2>/dev/null) || true
  _event_count=$(jq -r '.events_logged // 0' "$_state_file" 2>/dev/null) || true
  _last_cp=$(jq -r '.last_checkpoint // empty' "$_state_file" 2>/dev/null) || true
fi

if [ -n "$_run_path" ]; then
  echo "RECOVERED RUN: $_run_path"
  [ -n "$_phase" ] && echo "RECOVERED PHASE: $_phase | Events logged: ${_event_count:-0}"
  [ -n "$_last_cp" ] && echo "LAST CHECKPOINT: \"$_last_cp\""
  echo ""
  echo "NEXT STEPS:"
  echo "  1. Read .trw/frameworks/FRAMEWORK.md (compaction erased methodology context)"
  echo "  2. Call trw_session_start(query='your task domain') to reload learnings"
  echo "  3. Call trw_status() to confirm current phase"
  echo "  4. Resume from the last checkpoint — do not re-plan"
else
  echo "No active run found in pre-compaction snapshot."
  echo "Call trw_session_start() to check for any active run and reload learnings."
fi

echo ""
echo "MANDATORY: Read .trw/frameworks/FRAMEWORK.md before resuming work."
echo "WHY: Compaction erased your understanding of the 6-phase protocol, exit criteria,"
echo "  and quality gates. Skipping this produces methodology drift and rework."

log_hook_execution "PostCompact" "" "0"

exit 0
