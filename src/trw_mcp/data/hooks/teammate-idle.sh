#!/bin/sh
# PRD-FIX-024 / PRD-INFRA-010-FR01: TeammateIdle quality gate hook.
# Fires when an Agent Teams teammate is about to go idle.
# Exit 2 = keep working with stderr feedback. Exit 0 = allow idle.
# FR02: Blocks when teammate has uncompleted assigned tasks.
# FR04: TRW_SOFT_GATES=1 disables all blocking.
# Conditional hard gate: only blocks in team context, max 1 nudge per teammate.
# Fail-open: any infrastructure error silently exits 0.
set -e
_trw_intentional_exit=0
trap '[ "$_trw_intentional_exit" = "1" ] || exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

# Read stdin payload
_payload=$(cat) || exit 0
_teammate_name=""
_team_name=""
if command -v jq >/dev/null 2>&1; then
  _teammate_name=$(printf '%s' "$_payload" | jq -r '.teammate_name // empty' 2>/dev/null) || true
  _team_name=$(printf '%s' "$_payload" | jq -r '.team_name // empty' 2>/dev/null) || true
fi

# Fallback extraction without jq
if [ -z "$_teammate_name" ]; then
  _teammate_name=$(printf '%s' "$_payload" | grep -o '"teammate_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"teammate_name"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
fi

# Skip if we can't identify the teammate
[ -z "$_teammate_name" ] && exit 0

# FR04: Soft gates override
[ "${TRW_SOFT_GATES:-0}" = "1" ] && log_hook_execution "TeammateIdle" "${_teammate_name:-unknown}" "0:soft-gate" && exit 0

# If NOT in Agent Teams context, soft gate (for solo subagents)
if [ -z "$_team_name" ]; then
  log_hook_execution "TeammateIdle" "$_teammate_name" "0"
  exit 0
fi

# FR02: Hard gate — check for uncompleted assigned tasks
if [ -n "$_teammate_name" ] && [ -n "$_team_name" ]; then
  # Sanitize team name for filesystem path (prevent traversal)
  _safe_team=$(printf '%s' "$_team_name" | tr -c 'a-zA-Z0-9_-' '_' | head -c 64)
  _task_dir="$HOME/.claude/tasks/$_safe_team"
  if [ -d "$_task_dir" ]; then
    _incomplete_tasks=""
    for _task_file in "$_task_dir"/*.json "$_task_dir"/*.yaml; do
      [ -f "$_task_file" ] || continue
      _task_owner=""
      _task_status=""
      _task_subject=""
      if command -v jq >/dev/null 2>&1; then
        _task_owner=$(jq -r '.owner // empty' "$_task_file" 2>/dev/null) || true
        _task_status=$(jq -r '.status // empty' "$_task_file" 2>/dev/null) || true
        _task_subject=$(jq -r '.subject // empty' "$_task_file" 2>/dev/null) || true
      else
        _task_owner=$(grep -o '"owner"[[:space:]]*:[[:space:]]*"[^"]*"' "$_task_file" 2>/dev/null | head -1 | sed 's/.*"owner"[[:space:]]*:[[:space:]]*"//;s/"$//' ) || true
        _task_status=$(grep -o '"status"[[:space:]]*:[[:space:]]*"[^"]*"' "$_task_file" 2>/dev/null | head -1 | sed 's/.*"status"[[:space:]]*:[[:space:]]*"//;s/"$//' ) || true
        _task_subject=$(grep -o '"subject"[[:space:]]*:[[:space:]]*"[^"]*"' "$_task_file" 2>/dev/null | head -1 | sed 's/.*"subject"[[:space:]]*:[[:space:]]*"//;s/"$//' ) || true
      fi
      # Check if this task belongs to this teammate and is incomplete
      if [ "$_task_owner" = "$_teammate_name" ]; then
        if [ "$_task_status" = "in_progress" ] || [ "$_task_status" = "pending" ]; then
          _incomplete_tasks="${_incomplete_tasks}  - ${_task_subject:-unknown task}\n"
        fi
      fi
    done

    if [ -n "$_incomplete_tasks" ]; then
      _trw_intentional_exit=1
      printf 'TRW BLOCK: You have uncompleted tasks — complete them before going idle:\n%b' "$_incomplete_tasks" >&2
      log_hook_execution "TeammateIdle" "$_teammate_name" "2:tasks-incomplete"
      trap - EXIT
      exit 2
    fi
  fi
fi

# --- Team context: conditional hard gate (ceremony check) ---
_project_root="$(get_repo_root)" || exit 0
_context_dir="$_project_root/.trw/context"
[ -d "$_context_dir" ] || mkdir -p "$_context_dir" 2>/dev/null || exit 0

# Block count file: one per teammate to limit to 1 nudge each
_safe_name=$(printf '%s' "$_teammate_name" | tr -c 'a-zA-Z0-9_-' '_' | head -c 64)
_block_file="$_context_dir/idle_block_${_safe_name}"

# Check if we already nudged this teammate
_blocks=0
if [ -f "$_block_file" ]; then
  _blocks=$(tr -d '[:space:]' < "$_block_file" 2>/dev/null) || true
fi
_blocks=$((${_blocks:-0} + 0)) 2>/dev/null || _blocks=0

if [ "$_blocks" -ge 1 ]; then
  # Already nudged once — allow idle
  log_hook_execution "TeammateIdle" "$_teammate_name" "0"
  exit 0
fi

# Check if teammate has done meaningful work (checkpoint or learning)
_has_ceremony=0
_run_dir=$(find_active_run) || true
if [ -n "$_run_dir" ]; then
  _events_path="${_run_dir}meta/events.jsonl"
  if [ -f "$_events_path" ]; then
    if has_event "$_events_path" "checkpoint" || has_event "$_events_path" "learning_saved"; then
      _has_ceremony=1
    fi
  fi
fi

# Also check hook-executions.log for evidence of completed tasks
_hook_log="$_context_dir/hook-executions.log"
if [ -f "$_hook_log" ]; then
  if grep -q "TaskCompleted.*${_teammate_name}" "$_hook_log" 2>/dev/null; then
    _has_ceremony=1
  fi
fi

if [ "$_has_ceremony" -eq 1 ]; then
  # Teammate has done meaningful work — allow idle
  log_hook_execution "TeammateIdle" "$_teammate_name" "0"
  exit 0
fi

# Block: nudge teammate to checkpoint before going idle
_blocks=$((_blocks + 1))
printf '%s' "$_blocks" > "$_block_file" 2>/dev/null || true
echo "TRW: Checkpointing now preserves your work for the team lead to review. Call trw_checkpoint(message) with a summary of what you completed. (Nudge $_blocks/1)" >&2

log_hook_execution "TeammateIdle" "$_teammate_name" "2"

_trw_intentional_exit=1
exit 2
