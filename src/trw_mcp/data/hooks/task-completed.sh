#!/bin/sh
# PRD-FIX-024 / PRD-INFRA-004-FR01: TaskCompleted hook — conditional quality gate.
# Logs completion events. Blocks (exit 2) Agent Teams teammates who
# haven't run trw_build_check (FR01) or haven't checkpointed (FR03).
# TRW_SOFT_GATES=1 disables all blocking (FR04).
# Subagent completions are never blocked (they are intermediate steps).
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
_task_subject=""
_teammate_name=""
if command -v jq >/dev/null 2>&1; then
  _task_subject=$(printf '%s' "$_payload" | jq -r '.task_subject // empty' 2>/dev/null) || true
  _teammate_name=$(printf '%s' "$_payload" | jq -r '.teammate_name // empty' 2>/dev/null) || true
fi

# Fallback extraction without jq
if [ -z "$_task_subject" ]; then
  _task_subject=$(printf '%s' "$_payload" | grep -o '"task_subject"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"task_subject"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
fi

# FR04: Soft gates override
[ "${TRW_SOFT_GATES:-0}" = "1" ] && log_hook_execution "TaskCompleted" "${_teammate_name:-unknown}:${_task_subject}" "0:soft-gate" && exit 0

# If NOT a teammate (subagent intermediate), soft gate only
if [ -z "$_teammate_name" ]; then
  log_hook_execution "TaskCompleted" "${_teammate_name:-unknown}:${_task_subject}" "0"
  _missing=$(check_ceremony_status 2>/dev/null) || exit 0
  if [ -n "$_missing" ]; then
    echo "TRW NOTE: Ceremony pending — remember to call trw_deliver() when all tasks are done." >&2
  fi
  exit 0
fi

# FR01: Hard gate — check build status for teammate completions
_project_root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
_build_status="$_project_root/.trw/context/build-status.yaml"
_tests_passed=""
if [ -f "$_build_status" ]; then
  _tests_passed=$(grep '^tests_passed:' "$_build_status" 2>/dev/null | head -1 | sed 's/^tests_passed:[[:space:]]*//' | tr -d "'" | tr -d '"') || true
fi

if [ "$_tests_passed" != "true" ]; then
  _trw_intentional_exit=1
  if [ -z "$_tests_passed" ]; then
    echo "TRW BLOCK: Run trw_build_check before completing — no build record found." >&2
  else
    echo "TRW BLOCK: Run trw_build_check before completing — last build failed." >&2
  fi
  log_hook_execution "TaskCompleted" "$_teammate_name:${_task_subject}" "2:build-fail"
  trap - EXIT
  exit 2
fi

# --- Teammate context: conditional hard gate (checkpoint check, FR03) ---
_context_dir="$_project_root/.trw/context"
[ -d "$_context_dir" ] || mkdir -p "$_context_dir" 2>/dev/null || exit 0

# Block count file per (teammate, task) to limit to 1 block each
_safe_name=$(printf '%s' "$_teammate_name" | tr -c 'a-zA-Z0-9_-' '_' | head -c 64)
_safe_task=$(printf '%s' "$_task_subject" | tr -c 'a-zA-Z0-9_-' '_' | head -c 64)
_block_file="$_context_dir/tc_block_${_safe_name}_${_safe_task}"

# Check if we already blocked this task
_blocks=0
if [ -f "$_block_file" ]; then
  _blocks=$(tr -d '[:space:]' < "$_block_file" 2>/dev/null) || true
fi
_blocks=$((${_blocks:-0} + 0)) 2>/dev/null || _blocks=0

if [ "$_blocks" -ge 1 ]; then
  # Already blocked once — allow completion
  log_hook_execution "TaskCompleted" "${_teammate_name:-unknown}:${_task_subject}" "0"
  exit 0
fi

# Check if teammate has checkpointed during this run
_has_checkpoint=0
_run_dir=$(find_active_run) || true
if [ -n "$_run_dir" ]; then
  _events_path="${_run_dir}meta/events.jsonl"
  if [ -f "$_events_path" ] && has_event "$_events_path" "checkpoint"; then
    _has_checkpoint=1
  fi
fi

if [ "$_has_checkpoint" -eq 1 ]; then
  # Teammate has checkpointed — now check for completion artifact
  _completion_artifact=""
  if [ -n "$_run_dir" ]; then
    _scratch_dir="${_run_dir}scratch/tm-${_safe_name}/completions"
    # Check for any completion YAML (task-scoped or general)
    if [ -d "$_scratch_dir" ]; then
      _completion_artifact=$(find "$_scratch_dir" -name '*.yaml' -type f 2>/dev/null | head -1) || true
    fi
  fi

  if [ -n "$_completion_artifact" ]; then
    # Has checkpoint AND completion artifact — allow
    log_hook_execution "TaskCompleted" "${_teammate_name:-unknown}:${_task_subject}" "0:artifact-verified"
    exit 0
  fi

  # Has checkpoint but no completion artifact — block once to require self-review
  if [ "$_blocks" -ge 2 ]; then
    # Already blocked twice — allow completion (escape valve)
    log_hook_execution "TaskCompleted" "${_teammate_name:-unknown}:${_task_subject}" "0:escape-valve"
    exit 0
  fi

  _blocks=$((_blocks + 1))
  printf '%s' "$_blocks" > "$_block_file" 2>/dev/null || true
  echo "TRW: Before completing, write a completion artifact to scratch/tm-${_safe_name}/completions/ with:" >&2
  echo "  - fr_coverage: list of FRs addressed and how" >&2
  echo "  - files_changed: list of files you modified" >&2
  echo "  - tests_run: test command and result summary" >&2
  echo "  - self_review: any issues found during self-review" >&2
  echo "Then call trw_checkpoint with your summary. (Block $_blocks/2)" >&2

  log_hook_execution "TaskCompleted" "${_teammate_name:-unknown}:${_task_subject}" "2:needs-artifact"

  _trw_intentional_exit=1
  exit 2
fi

# Block: ask teammate to checkpoint before completing
_blocks=$((_blocks + 1))
printf '%s' "$_blocks" > "$_block_file" 2>/dev/null || true
echo "TRW: Running trw_checkpoint preserves your task results for the team lead. Call trw_checkpoint(message) with a summary of what you completed and which FRs you addressed. (Block $_blocks/2)" >&2

log_hook_execution "TaskCompleted" "${_teammate_name:-unknown}:${_task_subject}" "2"

_trw_intentional_exit=1
exit 2
