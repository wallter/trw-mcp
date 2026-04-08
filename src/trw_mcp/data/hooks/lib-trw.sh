#!/bin/sh
# Shared TRW hook utilities — sourced by other hooks.
# PRD-INFRA-002: Fail-open pattern, POSIX shell only.
#
# Usage: . "$(dirname "$0")/lib-trw.sh"

# get_repo_root: Resolve the project root portably.
# Priority: $CLAUDE_PROJECT_DIR (set by Claude Code) > git > $PWD fallback.
# All hooks MUST use this function instead of hardcoded paths.
get_repo_root() {
  if [ -n "${CLAUDE_PROJECT_DIR:-}" ]; then
    printf '%s' "$CLAUDE_PROJECT_DIR"
  elif _gr_root="$(git rev-parse --show-toplevel 2>/dev/null)" && [ -n "$_gr_root" ]; then
    printf '%s' "$_gr_root"
  else
    pwd
  fi
}

# get_task_root: Read task_root from .trw/config.yaml or default to "docs".
get_task_root() {
  _gtr_root="$(get_repo_root)" || { printf 'docs'; return; }
  _gtr_config="$_gtr_root/.trw/config.yaml"
  if [ -f "$_gtr_config" ]; then
    _gtr_val=$(grep '^task_root:' "$_gtr_config" | head -1 | sed 's/^task_root:[[:space:]]*//' | tr -d "'" | tr -d '"')
    [ -n "$_gtr_val" ] && printf '%s' "$_gtr_val" && return
  fi
  printf 'docs'
}

# find_active_run: Locate the most recently created run directory.
# Prints the path to the run directory, or empty string if none found.
# Returns 0 if found, 1 if not.
find_active_run() {
  _task_root="${1:-$(get_task_root)}"
  _project_root="$(get_repo_root 2>/dev/null)" || return 1
  _latest=""
  _latest_name=""

  # Helper: scan a directory tree for run.yaml files, updating _latest/_latest_name.
  # Run dirs are named like 20260211T061443Z-58062ed4 (UTC timestamp + hash),
  # so lexicographic sort on the basename finds the newest.
  # NOTE: We must NOT compare full paths — task directory names pollute the sort.
  _scan_runs() {
    for _task_dir in "$1"/*/; do
      [ -d "$_task_dir" ] || continue
      # Pattern 1: {root}/{task}/runs/{run_id}/meta/run.yaml (legacy docs/ layout)
      if [ -d "$_task_dir/runs" ]; then
        for _run_dir in "$_task_dir/runs"/*/; do
          [ -f "$_run_dir/meta/run.yaml" ] || continue
          _run_name="${_run_dir%/}"
          _run_name="${_run_name##*/}"
          if [ -z "$_latest" ] || expr "$_run_name" '>' "$_latest_name" >/dev/null; then
            _latest="$_run_dir"
            _latest_name="$_run_name"
          fi
        done
      fi
      # Pattern 2: {root}/{task}/{run_id}/meta/run.yaml (MCP .trw/runs/ layout)
      for _run_dir in "$_task_dir"/*/; do
        [ -f "$_run_dir/meta/run.yaml" ] || continue
        _run_name="${_run_dir%/}"
        _run_name="${_run_name##*/}"
        if [ -z "$_latest" ] || expr "$_run_name" '>' "$_latest_name" >/dev/null; then
          _latest="$_run_dir"
          _latest_name="$_run_name"
        fi
      done
    done
  }

  # Scan the configured task_root (e.g. docs/)
  _scan_runs "$_project_root/$_task_root"

  # Also scan .trw/runs/ — MCP trw_init creates runs here, not under task_root
  if [ -d "$_project_root/.trw/runs" ]; then
    _scan_runs "$_project_root/.trw/runs"
  fi

  if [ -n "$_latest" ]; then
    printf '%s' "$_latest"
    return 0
  fi
  return 1
}

# append_event: Append a JSON event line to events.jsonl.
# Args: $1=events_path, $2=event_type, $3=extra_json_fields (optional)
# Requires: date, printf. Uses jq if available, falls back to printf.
append_event() {
  _events_path="$1"
  _event_type="$2"
  _extra="${3:-}"
  _ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)" || _ts="unknown"

  if command -v jq >/dev/null 2>&1 && [ -n "$_extra" ]; then
    printf '{"ts":"%s","event":"%s",%s}\n' "$_ts" "$_event_type" "$_extra" >> "$_events_path"
  else
    printf '{"ts":"%s","event":"%s"}\n' "$_ts" "$_event_type" >> "$_events_path"
  fi
}

# has_event: Check if events.jsonl contains an event of a given type.
# Args: $1=events_path, $2=event_type
# Returns 0 if found, 1 if not.
has_event() {
  _path="$1"
  _type="$2"
  [ -f "$_path" ] || return 1
  grep -q "\"event\":[[:space:]]*\"$_type\"" "$_path" 2>/dev/null
}

# has_recent_deliver: Check if ANY run modified in the last N minutes has deliver_complete.
# Handles parallel Claude Code instances where each session owns a different run.
# Args: $1=max_age_minutes (default 240 = 4 hours)
# Returns 0 if found, 1 if not.
has_recent_deliver() {
  _hrd_max_age="${1:-240}"
  _hrd_task_root="$(get_task_root)"
  _hrd_root="$(get_repo_root 2>/dev/null)" || return 1

  # Helper: scan a directory for recent deliver events
  _hrd_scan() {
    for _hrd_task_dir in "$1"/*/; do
      [ -d "$_hrd_task_dir" ] || continue
      # Pattern 1: {root}/{task}/runs/{run_id}/ (legacy docs/ layout)
      if [ -d "$_hrd_task_dir/runs" ]; then
        for _hrd_run_dir in "$_hrd_task_dir/runs"/*/; do
          _hrd_events="${_hrd_run_dir}meta/events.jsonl"
          [ -f "$_hrd_events" ] || continue
          if find "$_hrd_events" -mmin "-$_hrd_max_age" 2>/dev/null | grep -q .; then
            if has_event "$_hrd_events" "trw_deliver_complete"; then
              return 0
            fi
          fi
        done
      fi
      # Pattern 2: {root}/{task}/{run_id}/ (MCP .trw/runs/ layout)
      for _hrd_run_dir in "$_hrd_task_dir"/*/; do
        _hrd_events="${_hrd_run_dir}meta/events.jsonl"
        [ -f "$_hrd_events" ] || continue
        if find "$_hrd_events" -mmin "-$_hrd_max_age" 2>/dev/null | grep -q .; then
          if has_event "$_hrd_events" "trw_deliver_complete"; then
            return 0
          fi
        fi
      done
    done
    return 1
  }

  # Scan configured task_root
  _hrd_scan "$_hrd_root/$_hrd_task_root" && return 0

  # Also scan .trw/runs/
  if [ -d "$_hrd_root/.trw/runs" ]; then
    _hrd_scan "$_hrd_root/.trw/runs" && return 0
  fi

  return 1
}

# infer_phase: Determine current execution phase from events.jsonl patterns.
# Prints one of: none, early, plan, implement, validate, deliver, done.
# Used by UserPromptSubmit hook for phase-calibrated output.
infer_phase() {
  _ip_run_dir=$(find_active_run) || { printf 'none'; return; }
  [ -n "$_ip_run_dir" ] || { printf 'none'; return; }

  _ip_events="${_ip_run_dir}meta/events.jsonl"
  [ -f "$_ip_events" ] || { printf 'none'; return; }

  # Check from most-advanced phase backwards
  if has_event "$_ip_events" "trw_deliver_complete"; then
    printf 'done'; return
  fi
  if has_event "$_ip_events" "reflection_complete" || has_event "$_ip_events" "trw_reflect_complete"; then
    printf 'deliver'; return
  fi
  if has_event "$_ip_events" "build_check_complete"; then
    printf 'validate'; return
  fi
  if has_event "$_ip_events" "file_modified"; then
    printf 'implement'; return
  fi
  if grep -q '"tool_name"[[:space:]]*:[[:space:]]*"trw_prd_validate"' "$_ip_events" 2>/dev/null; then
    printf 'plan'; return
  fi
  printf 'early'
}

# init_hook_timer: Capture start time for duration measurement.
# Call near the top of each hook script.
init_hook_timer() {
  _hook_start_epoch=$(date +%s 2>/dev/null) || _hook_start_epoch=0
}

# log_hook_execution: Append structured execution log line.
# Args: $1=event (e.g. "SessionStart"), $2=matcher, $3=exit_code
# Writes to .trw/context/hook-executions.log with rotation at 1000 lines.
log_hook_execution() {
  _le_event="${1:-unknown}"
  _le_matcher="${2:-}"
  _le_exit="${3:-0}"
  _le_ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)" || _le_ts="unknown"
  _le_end=$(date +%s 2>/dev/null) || _le_end=0
  _le_duration=$((_le_end - ${_hook_start_epoch:-0})) 2>/dev/null || _le_duration=0

  _le_root="$(get_repo_root 2>/dev/null)" || return 0
  _le_log="$_le_root/.trw/context/hook-executions.log"
  _le_dir="$(dirname "$_le_log")"
  [ -d "$_le_dir" ] || mkdir -p "$_le_dir" 2>/dev/null || return 0

  printf '%s event=%s matcher=%s exit=%s duration=%ss\n' \
    "$_le_ts" "$_le_event" "$_le_matcher" "$_le_exit" "$_le_duration" \
    >> "$_le_log" 2>/dev/null || return 0

  # Rotate: cap at 1000 lines
  if [ -f "$_le_log" ]; then
    _le_lines=$(wc -l < "$_le_log" 2>/dev/null | tr -d ' ') || _le_lines=0
    if [ "$_le_lines" -gt 1000 ] 2>/dev/null; then
      _le_tmp="${_le_log}.tmp"
      if tail -500 "$_le_log" > "$_le_tmp" 2>/dev/null; then
        mv "$_le_tmp" "$_le_log" 2>/dev/null || rm -f "$_le_tmp" 2>/dev/null
      else
        rm -f "$_le_tmp" 2>/dev/null
      fi
    fi
  fi
}

# check_ceremony_status: Check if TRW ceremony steps are complete.
# PRD-INFRA-004-FR03: Scans events.jsonl for required ceremony events.
# Prints formatted checklist of missing steps, or empty if all complete.
# Returns 0 if checked (output may be empty or contain missing steps).
# Returns 1 if no active run or event count < 3 (caller should skip).
check_ceremony_status() {
  _cs_run_dir=$(find_active_run) || return 1
  [ -n "$_cs_run_dir" ] || return 1

  _cs_events="${_cs_run_dir}meta/events.jsonl"
  [ -f "$_cs_events" ] || return 1

  _cs_count=$(wc -l < "$_cs_events" 2>/dev/null | tr -d ' ') || _cs_count=0
  [ "$_cs_count" -ge 3 ] 2>/dev/null || return 1

  # FR02: trw_deliver_complete short-circuits — all ceremony done
  if has_event "$_cs_events" "trw_deliver_complete"; then
    return 0
  fi

  # Check individual ceremony events
  _cs_missing=""
  if ! has_event "$_cs_events" "reflection_complete" && ! has_event "$_cs_events" "trw_reflect_complete"; then
    _cs_missing="${_cs_missing}, trw_reflect"
  fi
  if ! has_event "$_cs_events" "checkpoint"; then
    _cs_missing="${_cs_missing}, trw_checkpoint"
  fi
  if ! has_event "$_cs_events" "claude_md_synced"; then
    _cs_missing="${_cs_missing}, trw_claude_md_sync"
  fi

  if [ -n "$_cs_missing" ]; then
    # Strip leading ", "
    _cs_missing="${_cs_missing#, }"
    printf 'TRW BLOCK: Missing ceremony: %s. Run trw_deliver() to complete all. (%s events logged)' "$_cs_missing" "$_cs_count"
  fi
  return 0
}

# trw_enforcement_variant: Read enforcement_variant from .trw/config.yaml.
# Prints the configured variant (default: "baseline").
# CORE-074-FR09: A/B test infrastructure for ceremony enforcement.
trw_enforcement_variant() {
  _tev_config_file="$(get_repo_root 2>/dev/null)/.trw/config.yaml"
  if [ -f "$_tev_config_file" ]; then
    _tev_val=$(grep 'enforcement_variant:' "$_tev_config_file" | head -1 \
      | sed 's/^enforcement_variant:[[:space:]]*//' | tr -d "'" | tr -d '"' | tr -d '[:space:]')
    [ -n "$_tev_val" ] && printf '%s' "$_tev_val" && return
  fi
  printf 'baseline'
}

# trw_should_run_hooks: Return 0 (true) if hooks should run, 1 (false) if disabled by variant.
# Variants "mcp-only" and "none" disable hooks; all others allow them.
# CORE-074-FR09: A/B test infrastructure for ceremony enforcement.
trw_should_run_hooks() {
  _tsrh_variant="$(trw_enforcement_variant)"
  case "$_tsrh_variant" in
    mcp-only|none) return 1 ;;
    *) return 0 ;;
  esac
}

# cleanup_block_files: Remove stale per-teammate block count files.
# Called by session-end.sh as housekeeping.
# Args: $1=context_dir
cleanup_block_files() {
  _cbd_dir="${1:-}"
  [ -d "$_cbd_dir" ] || return 0
  rm -f "$_cbd_dir"/idle_block_* "$_cbd_dir"/tc_block_* 2>/dev/null || true
}

# cleanup_phase_cycle: Remove phase-cycle state files older than 4 hours.
# Called by session-end.sh as housekeeping.
# Args: $1=project_root (optional, defaults to get_repo_root)
cleanup_phase_cycle() {
  _cpc_root="${1:-$(get_repo_root 2>/dev/null)}"
  [ -n "$_cpc_root" ] || return 0
  _cpc_state="$_cpc_root/.claude/trw-phase-cycle.local.md"
  [ -f "$_cpc_state" ] || return 0
  # Remove if older than 4 hours (240 minutes)
  if find "$_cpc_state" -mmin "+240" 2>/dev/null | grep -q .; then
    rm -f "$_cpc_state" 2>/dev/null || true
  fi
}

# read_build_failures: Extract the failures list from build-status.yaml.
# Prints failures as newline-separated strings, or empty if none.
# Args: $1=build_status_path (optional, defaults to .trw/context/build-status.yaml)
read_build_failures() {
  _rbf_path="${1:-}"
  if [ -z "$_rbf_path" ]; then
    _rbf_root="$(get_repo_root 2>/dev/null)" || return 0
    _rbf_path="$_rbf_root/.trw/context/build-status.yaml"
  fi
  [ -f "$_rbf_path" ] || return 0
  # Extract list items under the 'failures:' key
  # Handles both inline '[]' and indented '- item' YAML list forms
  awk '
    /^failures:/ { in_list=1; next }
    in_list && /^[^[:space:]]/ { exit }
    in_list && /^[[:space:]]*-[[:space:]]+/ {
      sub(/^[[:space:]]*-[[:space:]]+/, "")
      print
    }
  ' "$_rbf_path" 2>/dev/null || true
}
