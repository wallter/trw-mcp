#!/bin/sh
# Shared TRW hook utilities — sourced by other hooks.
# PRD-INFRA-002: Fail-open pattern, POSIX shell only.
#
# Usage: . "$(dirname "$0")/lib-trw.sh"

# PRD-CORE-149 FR04/FR05: load the profile-resolved hook policy before any
# caller starts timers or emits output.  Keep the legacy TRW_HOOKS_ENABLED
# name as an input fallback, but normalize all shipped hooks onto the generated
# HOOKS_ENABLED / NUDGE_ENABLED contract.
_trw_hook_env_root="${CLAUDE_PROJECT_DIR:-}"
if [ -z "$_trw_hook_env_root" ]; then
  _trw_hook_env_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi
if [ -f "$_trw_hook_env_root/.trw/runtime/hook-env.sh" ]; then
  # shellcheck source=/dev/null
  . "$_trw_hook_env_root/.trw/runtime/hook-env.sh" 2>/dev/null || true
fi
HOOKS_ENABLED="${HOOKS_ENABLED:-${TRW_HOOKS_ENABLED:-true}}"
NUDGE_ENABLED="${NUDGE_ENABLED:-true}"
export HOOKS_ENABLED NUDGE_ENABLED
unset _trw_hook_env_root

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

# trw_pin_key: Print THIS session's pin key, or nothing.
# PRD-FIX-118 FR01/FR02.
#
# The key is TRW_SESSION_ID, exported by .trw/runtime/hook-env.sh from the
# client's own session variable (e.g. CLAUDE_CODE_SESSION_ID) -- the same string
# the MCP server keys .trw/runtime/pins.json on via resolve_pin_key. This
# function deliberately knows NOTHING about individual clients: the per-profile
# mapping lives in exactly one place (client_profiles/session_identity.py) and
# reaches here through the generated hook-env.sh. A stale hook-env.sh (written
# before FR01) or a client that publishes no identity therefore yields empty --
# the honest "identity unknown" state, not a guess.
#
# Args: $1=optional fallback key (e.g. the session_id from a hook's stdin
#       payload), used only when TRW_SESSION_ID is absent.
# Returns 0 and prints the key when known; 1 otherwise.
trw_pin_key() {
  _tpk_key="${TRW_SESSION_ID:-}"
  [ -n "$_tpk_key" ] || _tpk_key="${1:-}"
  [ -n "$_tpk_key" ] || return 1
  printf '%s' "$_tpk_key"
}

# resolve_owned_run: Print the run directory THIS session owns, or nothing.
# PRD-FIX-118 FR02 -- the single run-ownership primitive.
#
# Ownership means: an entry keyed by this session's own pin key exists in
# .trw/runtime/pins.json AND points at a real run inside this project. It is
# NEVER established by recency -- that is precisely the defect this replaces.
# An unowned session gets empty output and a non-zero status so its caller can
# emit an explicit unpinned state (FR04) instead of adopting a foreign run.
#
# Contract notes:
#   - Output ends with a trailing slash, matching find_active_run, so callers
#     can keep using "${run_dir}meta/events.jsonl".
#   - Read-only. Resolving ownership never pins, creates, or adopts a run
#     (NFR04): candidate runs stay advisory.
#   - Containment: a pin whose run_path escapes the project root is rejected,
#     mirroring the check already proven in post-tool-event.sh.
#   - Fail-open (NFR01): a missing, unreadable, or malformed pins.json, an
#     absent jq/python3, or a dangling run_path all resolve to "unowned".
#
# Args: $1=optional fallback pin key (see trw_pin_key).
# Returns 0 and prints the owned run dir; 1 otherwise.
resolve_owned_run() {
  _ror_key=$(trw_pin_key "${1:-}") || return 1
  [ -n "$_ror_key" ] || return 1

  _ror_root="$(get_repo_root 2>/dev/null)" || return 1
  _ror_root=$(cd "$_ror_root" 2>/dev/null && pwd -P) || return 1
  _ror_pins="$_ror_root/.trw/runtime/pins.json"
  [ -f "$_ror_pins" ] || return 1

  _ror_path=""
  if command -v jq >/dev/null 2>&1; then
    _ror_path=$(jq -r --arg sid "$_ror_key" '.[$sid].run_path // empty' "$_ror_pins" 2>/dev/null) || _ror_path=""
  elif command -v python3 >/dev/null 2>&1; then
    _ror_path=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], {}).get("run_path", ""))' "$_ror_pins" "$_ror_key" 2>/dev/null) || _ror_path=""
  fi
  [ -n "$_ror_path" ] || return 1
  [ -d "$_ror_path" ] || return 1
  _ror_path=$(cd "$_ror_path" 2>/dev/null && pwd -P) || return 1

  # Containment: reject a pin pointing outside this project's run layouts.
  _ror_task_root="$(get_task_root)"
  case "${_ror_path%/}/" in
    "${_ror_root%/}/.trw/runs/"*) : ;;
    "${_ror_root%/}/${_ror_task_root%/}/"*/runs/*/) : ;;
    *) return 1 ;;
  esac

  [ -f "${_ror_path%/}/meta/run.yaml" ] || return 1
  printf '%s/' "${_ror_path%/}"
}

# find_active_run: Locate the most recently created run directory.
# Prints the path to the run directory, or empty string if none found.
# Returns 0 if found, 1 if not.
#
# PRD-FIX-118 FR03: RECENCY FALLBACK ONLY. Newest-wins is correct only when this
# session's identity is unknowable (no pin key at all) -- with N instances live it
# otherwise hands the caller another session's run. New call sites MUST try
# resolve_owned_run first and reach this only when trw_pin_key returns empty.
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

# _json_escape: Escape a string for safe embedding as a JSON string value.
# Escapes backslash and double-quote, and strips/escapes control characters
# (newline, tab, CR, and other C0 controls + DEL) so an attacker-controlled
# value (e.g. a crafted file path or tool name) cannot break out of the JSON
# string and inject extra fields or split the JSONL line.
# Hooks MUST route any interpolated value through this before writing JSONL.
_json_escape() {
  printf '%s' "$1" \
    | sed 's/\\/\\\\/g; s/"/\\"/g' \
    | awk 'BEGIN{ORS=""} {gsub(/\t/,"\\t"); if(NR>1)printf "\\n"; printf "%s",$0}' \
    | tr -d '\000-\010\013\014\016-\037\177'
}

# append_event: Append a JSON event line to events.jsonl.
# Args: $1=events_path, $2=event_type, $3=extra_json_fields (optional)
# Requires: date, printf. Uses jq if available, falls back to printf.
# SECURITY: $2 (event_type) is JSON-escaped here. The caller is responsible
# for escaping any value embedded in $3 via _json_escape (see post-tool-event.sh).
append_event() {
  _events_path="$1"
  _event_type="$(_json_escape "$2")"
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
  # Escape BRE metacharacters in the event type so it is matched as a literal
  # (a value containing . * [ ] \ ^ $ would otherwise alter the pattern / match
  # the wrong events — regex injection).
  _type_esc=$(printf '%s' "$_type" | sed 's/[.[\*^$\\/]/\\&/g')
  grep -q "\"event\":[[:space:]]*\"$_type_esc\"" "$_path" 2>/dev/null
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

# has_recent_session_deliver: True when the session-scoped log records a recent
# deliver. An UNPINNED trw_deliver has no run events.jsonl to receive its
# completion marker, so it lands ONLY in .trw/context/session-events.jsonl. That
# signal is session-scoped and must be trusted regardless of which run
# find_active_run attributed to this session. The mtime recency bound stops an
# old, persisted marker in the append-only log from clearing every future
# session's nudge.
# Args: $1=max_age_minutes (default 240).
# Returns 0 if a recent session-scoped deliver exists, 1 otherwise.
has_recent_session_deliver() {
  _hrsd_max_age="${1:-240}"
  _hrsd_root="$(get_repo_root 2>/dev/null)" || return 1
  _hrsd_events="$_hrsd_root/.trw/context/session-events.jsonl"
  [ -f "$_hrsd_events" ] || return 1
  find "$_hrsd_events" -mmin "-$_hrsd_max_age" 2>/dev/null | grep -q . || return 1
  has_event "$_hrsd_events" "trw_deliver_complete"
}

# trw_stop_deliver_window_min: Recency window, in minutes, for the session-scoped
# tool-invocation deliver predicate below (PRD-FIX-117 FR01).
#
# The default is 240 — deliberately the SAME constant has_recent_deliver and
# has_recent_session_deliver already use — so the unpinned path inherits exactly
# the tolerance the pinned path has today rather than a newly invented, laxer one
# (PRD-FIX-117 OQ-01).
#
# Override precedence (first non-empty wins):
#   1. $TRW_STOP_DELIVER_WINDOW_MIN                      (env, per session)
#   2. stop_deliver_window_minutes: in .trw/config.yaml  (project)
#   3. 240                                               (default)
# A value of 0 disables the FR01 predicate entirely — the documented rollback
# knob: the reminder then fires exactly as it does today. A non-numeric value is
# ignored in favour of the default rather than silently disabling the gate.
trw_stop_deliver_window_min() {
  _tsdw_val="${TRW_STOP_DELIVER_WINDOW_MIN:-}"
  if [ -z "$_tsdw_val" ]; then
    _tsdw_cfg="$(get_repo_root 2>/dev/null)/.trw/config.yaml"
    if [ -f "$_tsdw_cfg" ]; then
      _tsdw_val=$(grep '^stop_deliver_window_minutes:' "$_tsdw_cfg" 2>/dev/null | head -1 \
        | sed 's/^stop_deliver_window_minutes:[[:space:]]*//' | tr -d "'\"" | tr -d '[:space:]')
    fi
  fi
  case "$_tsdw_val" in
    '' | *[!0-9]*) printf '240' ;;
    *) printf '%s' "$_tsdw_val" ;;
  esac
}

# has_recent_session_tool_deliver: True when .trw/context/session-events.jsonl
# records a SUCCESSFUL trw_deliver TOOL INVOCATION whose own row timestamp falls
# inside the recency window. PRD-FIX-117 FR01.
#
# Why this exists. The telemetry fallback path (the one taken precisely when no
# run directory resolves) writes an unpinned delivery as
#   {"event":"tool_invocation","tool_name":"trw_deliver","success":true,...}
# and NEVER as {"event":"trw_deliver_complete",...}. has_event matches the
# "event" field only, so has_recent_session_deliver above is structurally blind
# to unpinned deliveries — not merely unlucky. Measured on the live log
# 2026-07-24: 208 rows, 186 tool_invocation, 22 of them trw_deliver, and the
# trw_deliver_complete type effectively absent from the unpinned write path.
#
# Recency is evaluated on the ROW's own ts, not on the file's mtime, because
# session-events.jsonl is appended by every tool call: its mtime is always fresh,
# so a file-level window would let one ancient deliver row suppress the reminder
# for every future session. ISO-8601 UTC timestamps compare correctly as plain
# strings, so no per-row date parsing is needed.
#
# Cost (NFR03): a bounded tail of the log, never a full scan.
#   $TRW_SESSION_EVENT_TAIL_LINES (default 500).
#
# Fail-open (NFR01): an absent/unreadable log, an unusable date(1), a malformed
# row, or a non-numeric window all yield 1 ("no delivery observed") — i.e. TODAY's
# behaviour, the reminder still fires. A suppressed true positive is worse than a
# surviving false one.
#
# Args: $1=max_age_minutes (default: trw_stop_deliver_window_min).
# Returns 0 if a recent successful trw_deliver invocation exists, 1 otherwise.
has_recent_session_tool_deliver() {
  _hrstd_max_age="${1:-$(trw_stop_deliver_window_min)}"
  case "$_hrstd_max_age" in
    '' | *[!0-9]*) return 1 ;;
  esac
  [ "$_hrstd_max_age" -gt 0 ] 2>/dev/null || return 1

  _hrstd_root="$(get_repo_root 2>/dev/null)" || return 1
  _hrstd_events="$_hrstd_root/.trw/context/session-events.jsonl"
  [ -f "$_hrstd_events" ] || return 1
  [ -r "$_hrstd_events" ] || return 1

  # Cutoff as a lexicographically comparable UTC ISO-8601 prefix. GNU date first,
  # then BSD date; if neither works we cannot bound recency, so fail open.
  _hrstd_cut=$(date -u -d "$_hrstd_max_age minutes ago" '+%Y-%m-%dT%H:%M:%S' 2>/dev/null) || _hrstd_cut=""
  if [ -z "$_hrstd_cut" ]; then
    _hrstd_cut=$(date -u -v-"${_hrstd_max_age}"M '+%Y-%m-%dT%H:%M:%S' 2>/dev/null) || _hrstd_cut=""
  fi
  [ -n "$_hrstd_cut" ] || return 1

  _hrstd_tail="${TRW_SESSION_EVENT_TAIL_LINES:-500}"
  case "$_hrstd_tail" in
    '' | *[!0-9]* | 0) _hrstd_tail=500 ;;
  esac

  # Preferred path: jq parses each row exactly (field order, escaping, nesting).
  # fromjson? drops malformed lines instead of aborting the scan.
  if command -v jq >/dev/null 2>&1; then
    tail -n "$_hrstd_tail" "$_hrstd_events" 2>/dev/null | jq -e -R --arg cut "$_hrstd_cut" '
        (fromjson? // empty)
        | select(.event == "tool_invocation" and .tool_name == "trw_deliver" and .success == true)
        | select(((.ts // "") | tostring)[0:19] >= $cut)
      ' >/dev/null 2>&1 && return 0
    return 1
  fi

  # Fallback path (NFR02): no new runtime dependency. Same verdict, text match.
  tail -n "$_hrstd_tail" "$_hrstd_events" 2>/dev/null | awk -v cut="$_hrstd_cut" '
    /"event"[[:space:]]*:[[:space:]]*"tool_invocation"/ &&
    /"tool_name"[[:space:]]*:[[:space:]]*"trw_deliver"/ &&
    /"success"[[:space:]]*:[[:space:]]*true/ {
      if (match($0, /"ts"[[:space:]]*:[[:space:]]*"[^"]*"/)) {
        ts = substr($0, RSTART, RLENGTH)
        sub(/^"ts"[[:space:]]*:[[:space:]]*"/, "", ts)
        sub(/"$/, "", ts)
        if (substr(ts, 1, 19) >= cut) { found = 1; exit }
      }
    }
    END { exit(found ? 0 : 1) }
  '
}

# pin_run_path_for: Print the run_path pinned to a given session_id, or nothing.
# Reads .trw/runtime/pins.json (a session_id -> {run_path,...} map written by the
# MCP pin-isolation layer) so the Stop hook can attribute enforcement to THIS
# session's own run instead of a parallel instance's newest run. Uses jq when
# available; without jq it returns non-zero so the caller falls back to legacy
# behavior rather than guessing from a fragile multi-line grep.
# Args: $1=pins_json_path, $2=session_id.
# Returns 0 and prints the path when resolved; 1 otherwise.
pin_run_path_for() {
  _prp_pins="$1"
  _prp_sid="$2"
  [ -f "$_prp_pins" ] || return 1
  [ -n "$_prp_sid" ] || return 1
  command -v jq >/dev/null 2>&1 || return 1
  _prp_val=$(jq -r --arg sid "$_prp_sid" '.[$sid].run_path // empty' "$_prp_pins" 2>/dev/null) || return 1
  [ -n "$_prp_val" ] || return 1
  printf '%s' "$_prp_val"
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

# cleanup_block_files: Remove stale per-helper block count files.
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
