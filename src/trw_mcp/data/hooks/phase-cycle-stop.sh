#!/bin/sh
# PRD-CORE-100: Stop hook — phase exit criteria enforcement.
# Evaluates whether the current phase's exit criteria are met before allowing
# session termination. Blocks (exit 2) if criteria unmet, up to safety valve limits.
# After max_phase_iterations or max_total_cycles, warns and allows (fail-open).
# Uses mkdir as atomic lock (matching stop-ceremony.sh pattern).
# POSIX sh compatible — no bash-isms, no jq dependency.
set -e
_trw_pcs_intentional_exit=""
# Fail-open: any unhandled error allows exit
trap '[ -n "$_trw_pcs_intentional_exit" ] || exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

# PRD-CORE-149 FR05: light-mode profiles (HOOKS_ENABLED=false) short-circuit
# the entire hook with a no-op exit. The env file is sourced inside lib-trw.sh.
if [ "${HOOKS_ENABLED:-true}" = "false" ]; then
  exit 0
fi

init_hook_timer

# FR07: Read stdin payload for transcript_path (must happen before any stdin-consuming command)
_transcript_path=""
if ! [ -t 0 ]; then
  _stdin_payload=$(cat 2>/dev/null) || _stdin_payload=""
  if [ -n "$_stdin_payload" ]; then
    _transcript_path=$(printf '%s' "$_stdin_payload" \
      | grep -o '"transcript_path"[[:space:]]*:[[:space:]]*"[^"]*"' \
      | head -1 \
      | sed 's/.*"transcript_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/') || _transcript_path=""
  fi
fi

_project_root="$(get_repo_root)" || exit 0
_context_dir="$_project_root/.trw/context"
_state_file="$_project_root/.claude/trw-phase-cycle.local.md"
_lock_dir="$_context_dir/phase_cycle_stop.lock"

# Config overrides from environment
_max_phase_iter="${TRW_MAX_PHASE_ITERATIONS:-3}"
_max_total_cycles="${TRW_MAX_TOTAL_CYCLES:-6}"

# Validate numeric env overrides — fall back to defaults if non-numeric
_max_phase_iter=$(printf '%d' "$_max_phase_iter" 2>/dev/null) || _max_phase_iter=3
_max_total_cycles=$(printf '%d' "$_max_total_cycles" 2>/dev/null) || _max_total_cycles=6

# Require an active TRW run
_run_dir=$(find_active_run) || exit 0
[ -n "$_run_dir" ] || exit 0

_events_path="${_run_dir}meta/events.jsonl"
[ -f "$_events_path" ] || exit 0

# Need at least one event to bother checking
_event_count=$(wc -l < "$_events_path" 2>/dev/null | tr -d ' ') || _event_count=0
[ "$_event_count" -gt 0 ] 2>/dev/null || exit 0

# If trw_deliver_complete is already recorded, phase cycle is satisfied — allow
if has_event "$_events_path" "trw_deliver_complete"; then
  rm -f "$_state_file" 2>/dev/null || true
  log_hook_execution "Stop/phase-cycle" "" "0"
  exit 0
fi

# Determine current phase via lib-trw infer_phase
_current_phase="$(infer_phase)"

# Phases that always allow exit without criteria checks
case "$_current_phase" in
  none|done|early)
    log_hook_execution "Stop/phase-cycle-skip" "" "0"
    exit 0
    ;;
esac

# -----------------------------------------------------------------------
# Evaluate phase exit criteria — POSIX-only parsing (grep/sed/awk, no jq)
# -----------------------------------------------------------------------

# Returns 0 if criteria are met for the given phase, 1 if unmet.
_phase_criteria_met() {
  _pcm_phase="$1"
  _pcm_events="$2"
  _pcm_build_status="$_project_root/.trw/context/build-status.yaml"

  case "$_pcm_phase" in
    plan)
      # RESEARCH/PLAN: at least one trw_prd_validate or plan_updated event
      if has_event "$_pcm_events" "trw_prd_validate_complete" \
         || has_event "$_pcm_events" "plan_updated" \
         || grep -q '"tool_name"[[:space:]]*:[[:space:]]*"trw_prd_validate"' "$_pcm_events" 2>/dev/null; then
        return 0
      fi
      return 1
      ;;
    implement)
      # IMPLEMENT: at least one file_modified event
      if has_event "$_pcm_events" "file_modified"; then
        return 0
      fi
      return 1
      ;;
    validate)
      # VALIDATE: build_check_complete AND tests_passed=true in build-status.yaml
      if has_event "$_pcm_events" "build_check_complete"; then
        if [ -f "$_pcm_build_status" ]; then
          _tp=$(grep '^tests_passed:' "$_pcm_build_status" | head -1 \
            | sed 's/^tests_passed:[[:space:]]*//' | tr -d "'" | tr -d '"' | tr -d '[:space:]')
          if [ "$_tp" = "true" ]; then
            return 0
          fi
        fi
      fi
      return 1
      ;;
    deliver)
      # REVIEW/DELIVER: review_complete event with no review_finding_p0 events
      if has_event "$_pcm_events" "review_complete" \
         || has_event "$_pcm_events" "trw_reflect_complete"; then
        if ! has_event "$_pcm_events" "review_finding_p0"; then
          return 0
        fi
      fi
      return 1
      ;;
    *)
      # Unknown phase — allow
      return 0
      ;;
  esac
}

# -----------------------------------------------------------------------
# Check for phase reversion: tests failing in build-status.yaml
# -----------------------------------------------------------------------
_check_reversion_needed() {
  _crn_build_status="$_project_root/.trw/context/build-status.yaml"
  [ -f "$_crn_build_status" ] || return 1
  _crn_tp=$(grep '^tests_passed:' "$_crn_build_status" | head -1 \
    | sed 's/^tests_passed:[[:space:]]*//' | tr -d "'" | tr -d '"' | tr -d '[:space:]')
  [ "$_crn_tp" = "true" ] && return 1
  # tests_passed is not true — reversion may be needed
  return 0
}

# -----------------------------------------------------------------------
# FR06: Failure context extraction — max 200 chars per summary
# -----------------------------------------------------------------------
_json_escape_simple() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

_extract_failure_summary() {
  # Extracts a failure summary (max 200 chars) from current state
  # Sets _pcs_new_failure
  _efs_phase="$1"
  _efs_events="$2"
  _efs_build="$_project_root/.trw/context/build-status.yaml"
  _pcs_new_failure=""

  case "$_efs_phase" in
    validate)
      if [ -f "$_efs_build" ]; then
        _pcs_new_failure=$(grep '^  - ' "$_efs_build" | head -1 | sed 's/^[[:space:]]*-[[:space:]]*//')
      fi
      [ -z "$_pcs_new_failure" ] && _pcs_new_failure="VALIDATE: tests_passed=false"
      ;;
    deliver)
      if [ -f "$_efs_events" ]; then
        _pcs_new_failure=$(grep '"review_finding_p0"' "$_efs_events" | tail -1 | head -c 200)
      fi
      [ -z "$_pcs_new_failure" ] && _pcs_new_failure="REVIEW: P0 finding present"
      ;;
    implement)
      _pcs_new_failure="IMPLEMENT: no file_modified event"
      ;;
    plan)
      _pcs_new_failure="PLAN: no validation or plan event"
      ;;
    *)
      _pcs_new_failure="Phase ${_efs_phase}: exit criteria unmet"
      ;;
  esac
  # Truncate to 200 chars
  _pcs_new_failure=$(printf '%.200s' "$_pcs_new_failure")
}

_prepend_failure() {
  # Prepends new failure to comma-sep list, keeps last 3
  _pf_existing="$1"
  _pf_new="$2"
  if [ -z "$_pf_existing" ]; then
    printf '%s' "$_pf_new"
    return
  fi
  # Count entries and keep max 2 old + 1 new = 3
  _pf_trimmed=$(printf '%s' "$_pf_existing" | tr ',' '\n' | head -2 | tr '\n' ',' | sed 's/,$//')
  printf '%s,%s' "$_pf_new" "$_pf_trimmed"
}

# -----------------------------------------------------------------------
# FR04: Phase reversion evaluation
# -----------------------------------------------------------------------
_evaluate_reversion() {
  # Evaluates whether phase reversion should trigger
  # Sets _pcs_revert_to and _pcs_revert_reason
  _er_phase="$1"
  _er_events="$2"
  _er_prev_failures="$3"
  _er_new_failure="$4"
  _pcs_revert_to=""
  _pcs_revert_reason=""

  case "$_er_phase" in
    validate)
      # VALIDATE->IMPLEMENT: same test failing across 2 iterations
      [ -z "$_er_prev_failures" ] && return 0
      # Extract test token from new failure (file or function name)
      _er_token=$(printf '%s' "$_er_new_failure" \
        | grep -o '[A-Za-z_]*test[A-Za-z_]*\|[A-Za-z_]*\.py' | head -1)
      [ -z "$_er_token" ] && return 0
      case "$_er_prev_failures" in
        *"$_er_token"*)
          _pcs_revert_to="implement"
          _pcs_revert_reason="Same test failure repeated: $_er_token"
          ;;
      esac
      ;;
    deliver)
      # REVIEW->IMPLEMENT or REVIEW->PLAN
      if has_event "$_er_events" "review_finding_p0"; then
        _er_finding=$(grep '"review_finding_p0"' "$_er_events" | tail -1)
        case "$_er_finding" in
          *architecture*|*interface*|*design*|*"module boundary"*)
            _pcs_revert_to="plan"
            _pcs_revert_reason="P0 architectural finding requires PLAN phase"
            ;;
          *)
            _pcs_revert_to="implement"
            _pcs_revert_reason="P0 review finding requires code changes"
            ;;
        esac
      fi
      ;;
  esac
}

_emit_reversion_event() {
  _ere_events="$1"
  _ere_from="$2"
  _ere_to="$3"
  _ere_reason="$4"
  _ere_ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)" || _ere_ts="unknown"
  printf '{"ts":"%s","event":"phase_reversion","from_phase":"%s","to_phase":"%s","reason":"%s"}\n' \
    "$_ere_ts" "$_ere_from" "$_ere_to" "$(_json_escape_simple "$_ere_reason")" \
    >> "$_ere_events" 2>/dev/null || true
}

# -----------------------------------------------------------------------
# FR07: Completion promise verification
# -----------------------------------------------------------------------
_check_completion_promise() {
  # Checks transcript for PHASE_COMPLETE promise tag
  # Sets _pcs_promise_found (true/false)
  _ccp_transcript="$1"
  _pcs_promise_found=false
  [ -n "$_ccp_transcript" ] && [ -f "$_ccp_transcript" ] || return 0
  if grep -q '<promise>PHASE_COMPLETE</promise>' "$_ccp_transcript" 2>/dev/null; then
    _pcs_promise_found=true
  fi
}

# -----------------------------------------------------------------------
# State file I/O — YAML frontmatter, atomic writes
# -----------------------------------------------------------------------

_read_state() {
  # Reads state file fields into variables:
  # _st_phase, _st_iter, _st_total, _st_escalated, _st_prev_failures
  _st_phase="$_current_phase"
  _st_iter=0
  _st_total=0
  _st_escalated=false
  _st_prev_failures=""

  if [ ! -f "$_state_file" ]; then
    return 0
  fi

  _stf_phase=$(grep '^current_phase:' "$_state_file" | head -1 \
    | sed 's/^current_phase:[[:space:]]*//' | tr -d "'" | tr -d '"' | tr -d '[:space:]')
  _stf_iter=$(grep '^iteration_count:' "$_state_file" | head -1 \
    | sed 's/^iteration_count:[[:space:]]*//' | tr -d '[:space:]')
  _stf_total=$(grep '^total_cycles:' "$_state_file" | head -1 \
    | sed 's/^total_cycles:[[:space:]]*//' | tr -d '[:space:]')
  _stf_esc=$(grep '^escalation:' "$_state_file" | head -1 \
    | sed 's/^escalation:[[:space:]]*//' | tr -d "'" | tr -d '"' | tr -d '[:space:]')
  # Extract failures as a compact comma-separated string from the YAML list
  _stf_fail=$(grep '^  - ' "$_state_file" | sed 's/^[[:space:]]*-[[:space:]]*//' | tr '\n' ',' | sed 's/,$//')

  [ -n "$_stf_phase" ] && _st_phase="$_stf_phase"
  _st_iter=$(printf '%d' "${_stf_iter:-0}" 2>/dev/null) || _st_iter=0
  _st_total=$(printf '%d' "${_stf_total:-0}" 2>/dev/null) || _st_total=0
  [ -n "$_stf_esc" ] && _st_escalated="$_stf_esc"
  _st_prev_failures="$_stf_fail"
}

_write_state() {
  # Args: phase iter total escalated failures_line
  _ws_phase="${1:-$_current_phase}"
  _ws_iter="${2:-0}"
  _ws_total="${3:-0}"
  _ws_esc="${4:-false}"
  _ws_fail="${5:-}"

  _ws_ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)" || _ws_ts="unknown"
  _ws_tmp="${_state_file}.tmp.$$"

  # Build failures YAML list (keep last 3)
  _ws_fail_yaml=""
  if [ -n "$_ws_fail" ]; then
    # _ws_fail is comma-separated; emit up to 3 entries
    _ws_i=0
    _ws_old_ifs="$IFS"
    IFS=','
    for _ws_f in $_ws_fail; do
      [ "$_ws_i" -lt 3 ] || break
      [ -n "$_ws_f" ] || continue
      _ws_fail_yaml="${_ws_fail_yaml}  - ${_ws_f}
"
      _ws_i=$((_ws_i + 1))
    done
    IFS="$_ws_old_ifs"
  fi
  [ -n "$_ws_fail_yaml" ] || _ws_fail_yaml="  []\n"

  _ws_dir="$(dirname "$_ws_tmp")"
  [ -d "$_ws_dir" ] || mkdir -p "$_ws_dir" 2>/dev/null || return 0

  printf '---\ncurrent_phase: %s\niteration_count: %d\ntotal_cycles: %d\nmax_phase_iterations: %d\nmax_total_cycles: %d\nescalation: %s\nupdated_at: %s\nfailures:\n%s' \
    "$_ws_phase" "$_ws_iter" "$_ws_total" \
    "$_max_phase_iter" "$_max_total_cycles" \
    "$_ws_esc" "$_ws_ts" \
    "$_ws_fail_yaml" \
    > "$_ws_tmp" 2>/dev/null || { rm -f "$_ws_tmp" 2>/dev/null; return 0; }

  mv "$_ws_tmp" "$_state_file" 2>/dev/null || rm -f "$_ws_tmp" 2>/dev/null || true
}

# -----------------------------------------------------------------------
# Acquire lock — mkdir is atomic on POSIX. Fail-open if lock is held.
# -----------------------------------------------------------------------
[ -d "$_context_dir" ] || mkdir -p "$_context_dir" 2>/dev/null || exit 0
if ! mkdir "$_lock_dir" 2>/dev/null; then
  # Another Stop hook instance is running concurrently — allow this one through
  exit 0
fi
trap 'rm -rf "$_lock_dir" 2>/dev/null; [ -n "$_trw_pcs_intentional_exit" ] || exit 0' EXIT

# -----------------------------------------------------------------------
# Main logic (under lock)
# -----------------------------------------------------------------------
_read_state

# Reset iteration counter when phase changes
if [ "$_st_phase" != "$_current_phase" ]; then
  _st_iter=0
  _st_phase="$_current_phase"
fi

# Check if current phase criteria are met
if _phase_criteria_met "$_current_phase" "$_events_path"; then
  # Criteria met — clean up state and allow exit
  rm -f "$_state_file" 2>/dev/null || true
  log_hook_execution "Stop/phase-cycle-allow" "" "0"
  exit 0
fi

# Criteria not met — extract failure context (FR06) and check promise (FR07)
_new_iter=$((_st_iter + 1))
_new_total=$((_st_total + 1))

# FR06: Extract failure summary for this iteration
_extract_failure_summary "$_current_phase" "$_events_path"
_updated_failures=$(_prepend_failure "$_st_prev_failures" "$_pcs_new_failure")

# FR07: Check completion promise in transcript
_promise_warning=""
_check_completion_promise "$_transcript_path"
if [ "$_pcs_promise_found" = "true" ]; then
  _promise_warning=" WARNING: PHASE_COMPLETE promise detected but exit criteria not met. Do not output false completion promises."
fi

# FR04: Evaluate phase reversion (same failure twice, P0 review findings)
_evaluate_reversion "$_current_phase" "$_events_path" "$_st_prev_failures" "$_pcs_new_failure"
if [ -n "$_pcs_revert_to" ]; then
  _emit_reversion_event "$_events_path" "$_current_phase" "$_pcs_revert_to" "$_pcs_revert_reason"
  _write_state "$_pcs_revert_to" "0" "$_new_total" "false" "$_updated_failures"
  printf 'TRW REVERT: Phase %s -> %s. %s Fix the issue, then call trw_checkpoint() before continuing.%s\n' \
    "$_current_phase" "$_pcs_revert_to" "$_pcs_revert_reason" "$_promise_warning" >&2
  log_hook_execution "Stop/phase-cycle-revert" "" "2"
  _trw_pcs_intentional_exit=1
  exit 2
fi

# Safety valve: too many iterations in this phase or too many total cycles
if [ "$_new_iter" -gt "$_max_phase_iter" ] || [ "$_new_total" -gt "$_max_total_cycles" ]; then
  _write_state "$_current_phase" "$_new_iter" "$_new_total" "escalated" "$_updated_failures"
  printf 'TRW WARNING: Phase "%s" criteria not met after %d iterations (total %d). Safety valve — allowing exit.\n' \
    "$_current_phase" "$_new_iter" "$_new_total" >&2
  log_hook_execution "Stop/phase-cycle-escalate" "" "0"
  exit 0
fi

# Block: write updated state and exit 2
_write_state "$_current_phase" "$_new_iter" "$_new_total" "false" "$_updated_failures"

# FR06: Include most recent failure in blocking message
_recent_failure=""
if [ -n "$_pcs_new_failure" ]; then
  _recent_failure=" Last failure: ${_pcs_new_failure}."
fi

case "$_current_phase" in
  plan)
    _block_hint="Run trw_prd_validate() to confirm the plan."
    ;;
  implement)
    _block_hint="No file_modified event. Ensure implementation writes are complete."
    ;;
  validate)
    _block_hint="Run trw_build_check() and ensure tests pass."
    ;;
  deliver)
    _block_hint="Run trw_review() / trw_deliver() to complete delivery."
    ;;
  *)
    _block_hint="Complete the current phase."
    ;;
esac

printf 'TRW BLOCK [%s %d/%d]: %s%s%s Call trw_checkpoint() to save progress.\n' \
  "$_current_phase" "$_new_iter" "$_max_phase_iter" \
  "$_block_hint" "$_recent_failure" "$_promise_warning" >&2

log_hook_execution "Stop/phase-cycle-block" "" "2"

_trw_pcs_intentional_exit=1
exit 2
