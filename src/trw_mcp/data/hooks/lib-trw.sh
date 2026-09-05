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
#
# PRD-FIX-128 external audit row 8: `resolve_owned_run`'s containment check
# builds its glob from this value unquoted -- `"${root}/${task_root}/"*/runs/*/`
# -- so a `task_root` starting with `/` or containing a `..` segment could let
# a pin whose run_path literally contains that substring pass a containment
# check meant to keep run resolution inside the project. Reject those two
# shapes and fall back to the documented default rather than propagating a
# value this function cannot vouch for.
get_task_root() {
  _gtr_root="$(get_repo_root)" || { printf 'docs'; return; }
  _gtr_config="$_gtr_root/.trw/config.yaml"
  if [ -f "$_gtr_config" ]; then
    _gtr_val=$(grep '^task_root:' "$_gtr_config" | head -1 | sed 's/^task_root:[[:space:]]*//' | tr -d "'" | tr -d '"')
    case "$_gtr_val" in
      /* | *..*) _gtr_val="" ;;
    esac
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

# phase_from_events: Evaluate the phase ladder against a GIVEN events log.
# Prints one of: none, early, plan, implement, validate, deliver, done.
#
# PRD-FIX-124 FR11: this is the ONE implementation of the ladder. Callers that
# have already resolved a run (phase-cycle-stop.sh reads every exit criterion
# out of the run it owns) pass that run's events path here, so the phase and the
# criteria can never come from different runs. Callers that have not resolved a
# run use infer_phase below, which resolves one and delegates here.
#
# Args: $1=path to a run's meta/events.jsonl.
phase_from_events() {
  _pfe_events="$1"
  [ -n "$_pfe_events" ] || { printf 'none'; return; }
  [ -f "$_pfe_events" ] || { printf 'none'; return; }

  # Check from most-advanced phase backwards
  if has_event "$_pfe_events" "trw_deliver_complete"; then
    printf 'done'; return
  fi
  if has_event "$_pfe_events" "reflection_complete" || has_event "$_pfe_events" "trw_reflect_complete"; then
    printf 'deliver'; return
  fi
  if has_event "$_pfe_events" "build_check_complete"; then
    printf 'validate'; return
  fi
  if has_event "$_pfe_events" "file_modified"; then
    printf 'implement'; return
  fi
  if grep -q '"tool_name"[[:space:]]*:[[:space:]]*"trw_prd_validate"' "$_pfe_events" 2>/dev/null; then
    printf 'plan'; return
  fi
  printf 'early'
}

# infer_phase: Determine current execution phase for THIS session.
# Prints one of: none, early, plan, implement, validate, deliver, done.
# Used by the UserPromptSubmit hook for phase-calibrated output.
#
# PRD-FIX-124 FR11: ownership first, recency second. resolve_owned_run is the
# single run-ownership primitive (PRD-FIX-118 FR02); reaching find_active_run
# straight away — as this function used to — meant a parallel instance that had
# just called trw_deliver could pin THIS session's phase to "done". The recency
# fallback survives only for a genuinely unpinned session, where identity is
# unknowable and newest-wins is the honest best guess.
#
# Args: $1=optional session_id from the hook's stdin payload, used as the
#       fallback pin key when TRW_SESSION_ID is absent (see trw_pin_key).
infer_phase() {
  _ip_run_dir=$(resolve_owned_run "${1:-}" 2>/dev/null) || _ip_run_dir=""
  if [ -z "$_ip_run_dir" ]; then
    _ip_run_dir=$(find_active_run) || { printf 'none'; return; }
  fi
  [ -n "$_ip_run_dir" ] || { printf 'none'; return; }

  phase_from_events "${_ip_run_dir}meta/events.jsonl"
}

# init_hook_timer: Capture start time for duration measurement.
# Call near the top of each hook script.
init_hook_timer() {
  _hook_start_epoch=$(date +%s 2>/dev/null) || _hook_start_epoch=0
}

# log_hook_execution: Append structured execution log line.
# Args: $1=event (e.g. "SessionStart"), $2=matcher, $3=exit_code,
#       $4=optional detail — trailing `key=value` pairs appended after
#          `duration=`, used by the UserPromptSubmit auto-recall diagnostic
#          (PRD-FIX-124 FR05). A three-argument call produces a line byte-
#          identical to the pre-FR05 format, so every other hook is unaffected.
# Writes to .trw/context/hook-executions.log with rotation at 1000 lines.
#
# PRD-FIX-128 external audit row 11: this is a plain-text APPEND-ONLY file
# with no severity-level concept and no default install that tails, alerts
# on, or otherwise surfaces it to an operator. A caller's own "at INFO" claim
# about a record it writes here describes that record's intended importance,
# not this helper's delivery guarantee -- an operator who wants the signal
# has to go read this file.
log_hook_execution() {
  _le_event="${1:-unknown}"
  _le_matcher="${2:-}"
  _le_exit="${3:-0}"
  _le_detail="${4:-}"
  _le_ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)" || _le_ts="unknown"
  _le_end=$(date +%s 2>/dev/null) || _le_end=0
  _le_duration=$((_le_end - ${_hook_start_epoch:-0})) 2>/dev/null || _le_duration=0

  _le_root="$(get_repo_root 2>/dev/null)" || return 0
  _le_log="$_le_root/.trw/context/hook-executions.log"
  _le_dir="$(dirname "$_le_log")"
  [ -d "$_le_dir" ] || mkdir -p "$_le_dir" 2>/dev/null || return 0

  if [ -n "$_le_detail" ]; then
    printf '%s event=%s matcher=%s exit=%s duration=%ss %s\n' \
      "$_le_ts" "$_le_event" "$_le_matcher" "$_le_exit" "$_le_duration" "$_le_detail" \
      >> "$_le_log" 2>/dev/null || return 0
  else
    printf '%s event=%s matcher=%s exit=%s duration=%ss\n' \
      "$_le_ts" "$_le_event" "$_le_matcher" "$_le_exit" "$_le_duration" \
      >> "$_le_log" 2>/dev/null || return 0
  fi

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

# ===========================================================================
# PRD-CORE-247-FR01/FR02: absent-MCP-surface detection and the offline protocol
# ===========================================================================
#
# No hook can ASK the client whether MCP attached — the client exposes that only
# in its own UI, and by the time it gives up (120000 ms in the reported failure)
# SessionStart has long since run. So the surface's absence is inferred from the
# absence of the trace a healthy session leaves: a `tool_invocation` row whose
# `tool_name` begins `trw_`.
#
# A liveness probe is deliberately NOT used. `trw-mcp --version` measured
# 1.25-1.27 s over N=3 against these hooks' ~23 ms / ~71 ms budgets, and it
# answers the wrong question — whether the binary launches, not whether the
# client attached.
#
# **Fail-open runs toward "present", and that direction is the opposite of
# has_recent_session_tool_deliver's.** There, a suppressed true positive was
# worse than a surviving false one. Here the costs invert: a missed outage
# leaves today's behaviour, which is bad but familiar, while a FALSE degraded
# verdict would tell a healthy agent to abandon the gated path for one that
# records `gate_evaluated: false` — manufacturing exactly the ungated delivery
# the deliver gate exists to prevent. Every unreadable, absent, or malformed
# input therefore resolves to "the surface is present" and emits nothing.

# Repo-relative DIRECTORIES for the two markers this subsystem owns, holding one
# file per session key (PRD-FIX-128-FR02/FR03). Both used to be a single
# project-scoped FILE, which is how a six-session checkout came to share one
# epoch: the newest SessionStart owned the timestamp every peer measured itself
# against, the prompt counter summed everyone's prompts, and the first session
# to emit silenced the other five along with their framework directive.
_TRW_EPOCH_REL=".trw/runtime/session-epoch"
_TRW_DEGRADED_LATCH_REL=".trw/runtime/degraded-mode"

# trw_degraded_marker_path: Print THIS session's marker path for $1, or nothing.
#
# PRD-FIX-128-FR04/NFR03. The ONLY place a marker path is constructed -- the
# writer, both epoch readers, the latch trio, the SessionEnd reclamation and the
# SessionStart sweep all come through here -- so the key-admissibility rule
# below is defined exactly once rather than once per call site.
#
# A session identifier becomes a path component here, so it is validated BEFORE
# any filesystem call: at most 200 characters, drawn from letters, digits, dot,
# underscore and hyphen, with no `..` segment. The length bound is the one
# client_profiles/session_identity.py enforces on the server side, and the
# character rule is a STRICT SUBSET of what that module admits, so a key this
# function accepts is always a key the server accepts. Anything else is
# unresolvable, and an unresolvable key yields no path -- which is what lets the
# detector make no claim at all rather than a claim about the wrong session.
#
# Args: $1=kind (epoch|latch), $2=optional payload key (see trw_pin_key).
# Returns 0 and prints the absolute marker path; 1 printing nothing otherwise.
trw_degraded_marker_path() {
  case "${1:-}" in
    epoch) _tdmp_rel="$_TRW_EPOCH_REL" ;;
    latch) _tdmp_rel="$_TRW_DEGRADED_LATCH_REL" ;;
    *) return 1 ;;
  esac
  _tdmp_key=$(trw_pin_key "${2:-}") || return 1
  [ -n "$_tdmp_key" ] || return 1
  # PRD-FIX-128 external audit row 1: a bare `.` or `..`, or a leading `-`, all
  # pass the character-class test below (every one of those characters is in
  # the admitted set) but are not a session identifier -- `.`/`..` resolve the
  # marker PATH to the directory itself rather than an entry inside it, and a
  # leading `-` risks being read as an option flag by any tool this key is
  # later passed to. Reject the three shapes explicitly, before the general
  # character-class check.
  case "$_tdmp_key" in
    . | .. | -*) return 1 ;;
  esac
  case "$_tdmp_key" in
    *[!A-Za-z0-9._-]* | *..*) return 1 ;;
  esac
  [ "${#_tdmp_key}" -le 200 ] || return 1
  _tdmp_root="$(get_repo_root 2>/dev/null)" || return 1
  [ -n "$_tdmp_root" ] || return 1
  printf '%s/%s/%s' "$_tdmp_root" "$_tdmp_rel" "$_tdmp_key"
}

# _trw_degraded_dir_ready: Make directory $1 usable, migrating a pre-FR02
# legacy REGULAR FILE of the same name out of the way first.
#
# Turning the pre-FR02 single FILE into a directory of the same name requires
# removing that file, so the removal is the mkdir PRECONDITION and nothing else:
# there is no migration limb to retire later and no release in which it can be
# deleted, because a downgrade recreates the file and the next upgrade removes
# it again. No reader ever consults the legacy path, so a stale file left by a
# partially-upgraded peer cannot produce a verdict.
_trw_degraded_dir_ready() {
  if [ -f "$1" ]; then
    rm -f "$1" 2>/dev/null || return 1
  fi
  mkdir -p "$1" 2>/dev/null || return 1
  return 0
}

# _trw_degraded_marker_dir_ready: Make $1's parent directory usable (see
# _trw_degraded_dir_ready). $1 is a full MARKER path, not the directory itself.
_trw_degraded_marker_dir_ready() {
  _trw_degraded_dir_ready "$(dirname "$1")"
}

# _sanitize_context_text: Neutralize untrusted text before it reaches AI context.
#
# Moved here from session-start.sh (PRD-CORE-247-NFR03) so the SessionStart and
# UserPromptSubmit hooks share ONE implementation rather than growing a second,
# weaker copy in the prompt hook. Every value read from pre_compact_state.json,
# pins.json, the session epoch marker, or session-events.jsonl passes through it:
# all are machine-written but process-writable, so all are untrusted input to a
# prompt-injection surface.
#
#   - strips ASCII control chars (incl. CR/LF/ESC/BEL) so it stays single-line
#   - collapses runs of whitespace
#   - neutralizes common prompt-injection role/instruction markers
#   - bounds length to 200 chars
_sanitize_context_text() {
  printf '%s' "$1" \
    | tr -d '\000-\037\177' \
    | tr -s '[:space:]' ' ' \
    | sed -e 's/[][<>`]/ /g' \
          -e 's/\$(/ (/g' \
          -e 's/${/ {/g' \
          -e 's/[Ss][Yy][Ss][Tt][Ee][Mm][[:space:]]*:/system_/g' \
          -e 's/[Aa][Ss][Ss][Ii][Ss][Tt][Aa][Nn][Tt][[:space:]]*:/assistant_/g' \
          -e 's/[Uu][Ss][Ee][Rr][[:space:]]*:/user_/g' \
          -e 's/\[\/*[Ii][Nn][Ss][Tt][^]]*\]/ /g' \
    | cut -c1-200
}

# trw_config_int: Read a bounded integer tunable.
# Precedence: $2 (env value) > `<key>:` in .trw/config.yaml > $3 (default).
# A value that is non-numeric or outside [$4, $5] falls back to the default
# rather than propagating — a mistyped knob must not widen a safety verdict.
# Args: $1=config key, $2=env value (may be empty), $3=default, $4=min, $5=max.
trw_config_int() {
  _tci_val="${2:-}"
  if [ -z "$_tci_val" ]; then
    _tci_cfg="$(get_repo_root 2>/dev/null)/.trw/config.yaml"
    if [ -f "$_tci_cfg" ]; then
      _tci_val=$(grep -m1 "^$1:" "$_tci_cfg" 2>/dev/null \
        | sed "s/^$1:[[:space:]]*//" | tr -d "'\"" | tr -d '[:space:]') || _tci_val=""
    fi
  fi
  case "$_tci_val" in
    '' | *[!0-9]*) printf '%s' "$3"; return ;;
  esac
  if [ "$_tci_val" -lt "$4" ] 2>/dev/null || [ "$_tci_val" -gt "$5" ] 2>/dev/null; then
    printf '%s' "$3"
    return
  fi
  printf '%s' "$_tci_val"
}

# The three typed tunables this subsystem reads. Defaults and bounds mirror the
# Pydantic fields in models/config/_fields_degraded_mode.py -- corrected from
# _fields_ceremony.py, which has not declared them since they were split out --
# and the admission records in models/config/_field_admission_degraded_mode.py
# carry the rationale for each. A shell hook cannot import Pydantic, so these
# copies are pinned equal to the typed defaults by
# test_core_247_degraded_mode_hooks.py::test_session_markers_are_reclaimed
# rather than left to drift.
trw_degraded_grace_seconds() {
  trw_config_int degraded_detect_grace_seconds "${TRW_DEGRADED_DETECT_GRACE_SECONDS:-}" 180 30 900
}

trw_degraded_min_prompts() {
  trw_config_int degraded_detect_min_prompts "${TRW_DEGRADED_DETECT_MIN_PROMPTS:-}" 2 1 10
}

# PRD-FIX-128-FR08: the SECONDARY bound on the marker sweep. Pin liveness is the
# primary gate and is checked first; this age applies only to an identity with
# no live pin record.
trw_degraded_marker_retention_hours() {
  trw_config_int degraded_marker_retention_hours "${TRW_DEGRADED_MARKER_RETENTION_HOURS:-}" 24 1 168
}

# PRD-FIX-128 external audit row 7: the same typed-tunable pattern as the three
# accessors above, replacing a raw, unbounded env-var read
# (TRW_SESSION_EVENT_TAIL_LINES, still the env override name for back-compat).
trw_degraded_event_tail_lines() {
  trw_config_int degraded_event_tail_lines "${TRW_SESSION_EVENT_TAIL_LINES:-}" 500 50 2000
}

# trw_write_session_epoch: Stamp this session's start. Called by SessionStart.
#
# Two fields and nothing else (NFR03): an ISO-8601 UTC timestamp and an integer
# prompt counter. No path, no session identifier, no free text — the file is
# echoed into agent context by the offline block, so it carries no payload
# surface worth attacking. The identifier lives in the marker's NAME, which is
# the already-validated key (PRD-FIX-128-NFR03), never in its contents.
#
# Written per session key (PRD-FIX-128-FR02), so a peer's startup can no longer
# reset this session's clock or its prompt counter. With no resolvable identity
# nothing is created and the caller still succeeds: an absent marker is the
# existing fail-open case that resolves to "the surface is present".
#
# Args: $1=optional payload session id (see trw_pin_key).
trw_write_session_epoch() {
  _twse_ts=$(date -u '+%Y-%m-%dT%H:%M:%S' 2>/dev/null) || return 0
  [ -n "$_twse_ts" ] || return 0
  _twse_path=$(trw_degraded_marker_path epoch "${1:-}") || return 0
  _trw_degraded_marker_dir_ready "$_twse_path" || return 0
  printf '%s\n0\n' "$_twse_ts" > "$_twse_path" 2>/dev/null || return 0
  # FR08: reclaim siblings AFTER this session's own marker exists, so its fresh
  # mtime is what the sweep sees. The sweep skips this key explicitly anyway.
  trw_degraded_sweep_markers "${_twse_path##*/}"
  return 0
}

# _trw_pin_rows: Print one `key<TAB>pid<TAB>last_heartbeat_ts` row per pin.
#
# Returns 1, printing nothing, when the pin store cannot be read AS A PIN STORE
# -- absent, unreadable, not valid JSON, or not an object of objects. The caller
# turns that into "prune nothing", so an unparseable store can never widen
# reclamation. Same jq -> python3 -> give-up ladder resolve_owned_run uses, so
# the sweep introduces no new runtime dependency.
_trw_pin_rows() {
  [ -f "$1" ] || return 1
  [ -r "$1" ] || return 1
  # PRD-FIX-128 external audit row 5: a non-dict VALUE (e.g. `{"s1": null}`) is
  # skipped per-entry by both parsers rather than aborting the whole scan on
  # one malformed row. Before this fix jq silently tolerated it (indexing null
  # yields null, coalesced to "-") while python3 raised and discarded every
  # OTHER, well-formed entry too -- so the same pins.json pruned peers on a
  # jq host and pruned nothing at all on a python3-only host. Skipping is the
  # shared, more permissive direction: one malformed row must not disable
  # reclamation for every other identity in the store.
  if command -v jq >/dev/null 2>&1; then
    jq -r 'if type == "object" then (to_entries[]
        | select(.value | type == "object")
        | [.key, ((.value.pid // "-") | tostring), ((.value.last_heartbeat_ts // "-") | tostring)]
        | @tsv) else error("pins.json is not an object") end' "$1" 2>/dev/null || return 1
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import json,sys
d = json.load(open(sys.argv[1]))
if not isinstance(d, dict):
    raise SystemExit(1)
for k, v in d.items():
    if not isinstance(v, dict):
        continue
    print("%s\t%s\t%s" % (k, v.get("pid", "-"), v.get("last_heartbeat_ts", "-")))' "$1" 2>/dev/null || return 1
  else
    return 1
  fi
}

# _trw_pin_is_live: 0 when the pin described by $1/$2 is NOT provably expired.
#
# Mirrors the server's own predicate at state/_pin_ttl.py::pin_entry_is_expired
# rather than inventing a second one -- the hook and the server disagreeing about
# which sessions exist is exactly the class of defect this PRD is fixing. Expired
# requires BOTH that the recorded creator process is gone AND that a parseable
# heartbeat is older than pin_ttl_hours; a non-positive TTL disables expiry
# entirely, and anything unparseable resolves to "live", i.e. keep.
#
# Args: $1=pid, $2=heartbeat, $3=pin_ttl_hours, $4=now as epoch seconds.
_trw_pin_is_live() {
  [ "$3" -gt 0 ] 2>/dev/null || return 0
  case "$1" in
    '' | *[!0-9]*) return 0 ;;
  esac
  # `kill -0` reports EPERM for a live process owned by another user, so /proc is
  # consulted too: "we may not signal it" is not evidence that it is gone.
  kill -0 "$1" 2>/dev/null && return 0
  [ -d "/proc/$1" ] && return 0
  [ -n "$4" ] || return 0
  _tpil_hb=$(date -u -d "$2" '+%s' 2>/dev/null) || _tpil_hb=""
  [ -n "$_tpil_hb" ] || return 0
  [ $(((${4} - _tpil_hb) / 3600)) -gt "$3" ] 2>/dev/null || return 0
  return 1
}

# _trw_live_pin_keys: Print one key per line for every identity with a live pin.
# Returns 1, printing nothing, when the pin store is unusable (see _trw_pin_rows).
_trw_live_pin_keys() {
  _tlpk_root="$(get_repo_root 2>/dev/null)" || return 1
  _tlpk_rows=$(_trw_pin_rows "$_tlpk_root/.trw/runtime/pins.json") || return 1
  _tlpk_ttl=$(trw_config_int pin_ttl_hours "" 24 0 8760)
  _tlpk_now=$(date -u '+%s' 2>/dev/null) || _tlpk_now=""
  printf '%s\n' "$_tlpk_rows" | while IFS="$(printf '\t')" read -r _tlpk_k _tlpk_pid _tlpk_hb; do
    [ -n "$_tlpk_k" ] || continue
    if _trw_pin_is_live "$_tlpk_pid" "$_tlpk_hb" "$_tlpk_ttl" "$_tlpk_now"; then
      printf '%s\n' "$_tlpk_k"
    fi
  done
  return 0
}

# trw_degraded_sweep_markers: Reclaim sibling markers. LIVENESS FIRST, age second.
#
# PRD-FIX-128-FR08. One marker per session means markers accumulate, so the
# SessionStart startup that writes this session's epoch also sweeps the two
# directories. The decision table, in order:
#
#   1. identity holds a live pin        -> KEEP, whatever the marker's age
#   2. identity's pin record is expired -> apply the age bound
#   3. identity has no pin record       -> apply the age bound
#   4. pin store absent/unreadable/bad  -> KEEP EVERYTHING, prune nothing
#
# Liveness comes first because a long idle session is exactly the shape a pure
# age bound gets wrong: it is silent by definition, so its marker is old, and
# deleting it would reset the clock of the session most likely to be judged
# degraded next. The two failure directions are not symmetric -- a stale marker
# costs bytes, a wrongly-removed one costs a session its epoch -- so every
# unreadable input errs toward keeping.
#
# Cost: ONE metadata-only find over both directories. Finding nothing stale
# returns before the pin store is opened at all, which is the common case and
# what keeps SessionStart inside its NFR01 budget.
#
# Args: $1=this session's key, never pruned.
trw_degraded_sweep_markers() {
  _tdsm_self="${1:-}"
  _tdsm_root="$(get_repo_root 2>/dev/null)" || return 0

  # PRD-FIX-128 external audit rows 2/3: migrate BOTH legacy regular files (the
  # pre-FR02 single shared epoch/latch marker) to directories before anything
  # below inspects them. Without this, an unmigrated `.trw/runtime/degraded-mode`
  # regular file is invisible to the fast-path glob just below (it is not a
  # directory, so `dir/*` never expands) but IS visible to `find`'s -maxdepth 1
  # -type f, which reports the path itself at depth 0 -- so a legacy file could
  # be read as a stale marker, keyed "degraded-mode", and deleted. Doing the
  # migration unconditionally here, ahead of every other limb, is cheaper than
  # auditing every call site that could reach this state first: it is one
  # `[ -f ]` test per directory, borne once per SessionStart.
  _trw_degraded_dir_ready "$_tdsm_root/$_TRW_EPOCH_REL" 2>/dev/null || true
  _trw_degraded_dir_ready "$_tdsm_root/$_TRW_DEGRADED_LATCH_REL" 2>/dev/null || true

  # Fast path, and the one that matters for NFR01: a checkout with no marker but
  # this session's own has nothing to reclaim, and establishing that costs ZERO
  # forks -- glob expansion and parameter substitution only. Everything below
  # runs solely in the multi-session case that needs it.
  _tdsm_others=0
  for _tdsm_p in "$_tdsm_root/$_TRW_EPOCH_REL"/* "$_tdsm_root/$_TRW_DEGRADED_LATCH_REL"/*; do
    [ -f "$_tdsm_p" ] || continue
    [ "${_tdsm_p##*/}" != "$_tdsm_self" ] || continue
    _tdsm_others=1
    break
  done
  [ "$_tdsm_others" = 1 ] || return 0

  _tdsm_minutes=$(($(trw_degraded_marker_retention_hours) * 60))
  _tdsm_stale=$(find "$_tdsm_root/$_TRW_EPOCH_REL" "$_tdsm_root/$_TRW_DEGRADED_LATCH_REL" \
    -maxdepth 1 -type f -mmin "+$_tdsm_minutes" 2>/dev/null || true)
  [ -n "$_tdsm_stale" ] || return 0

  _tdsm_live=$(_trw_live_pin_keys) || return 0
  printf '%s\n' "$_tdsm_stale" | while IFS= read -r _tdsm_path; do
    [ -n "$_tdsm_path" ] || continue
    _tdsm_key="${_tdsm_path##*/}"
    [ "$_tdsm_key" != "$_tdsm_self" ] || continue
    case "
$_tdsm_live
" in
      *"
$_tdsm_key
"*) continue ;;
    esac
    rm -f "$_tdsm_path" 2>/dev/null || true
  done
  return 0
}

# trw_degraded_release_markers: Remove THIS session's epoch marker and latch.
#
# PRD-FIX-128-FR08, called by SessionEnd BEFORE its run-scoped early exits: a
# session that ended without owning a run still has markers to reclaim, and
# leaving them for the age-based sweep would keep a dead session's epoch around
# for a day. Nothing else is touched -- a peer's marker is never removed here.
trw_degraded_release_markers() {
  _tdrm_path=$(trw_degraded_marker_path epoch "${1:-}") || _tdrm_path=""
  if [ -n "$_tdrm_path" ]; then
    rm -f "$_tdrm_path" 2>/dev/null || true
  fi
  _tdrm_path=$(trw_degraded_marker_path latch "${1:-}") || _tdrm_path=""
  if [ -n "$_tdrm_path" ]; then
    rm -f "$_tdrm_path" 2>/dev/null || true
  fi
  return 0
}

# trw_clear_degraded_latch: Drop the "already told them" latch.
#
# Called on the `startup` SessionStart source ONLY. `resume`, `compact`, and
# `clear` all happen inside a session whose transport state has not changed, so
# clearing there would re-emit the block and, worse, would un-suppress the FR07
# framework directive for a session that still has no tools to apply it with.
# Args: $1=optional payload session id. PRD-FIX-128-FR03: this removes only
# THIS session's latch, so a peer's startup can no longer re-arm five other
# sessions' detectors (or, before that, silence them).
trw_clear_degraded_latch() {
  _tcdl_path=$(trw_degraded_marker_path latch "${1:-}") || return 0
  # PRD-FIX-128 external audit row 2: this is the one `startup`-only call site
  # that is guaranteed to run once per genuinely new session even when the
  # sweep's own migration (trw_degraded_sweep_markers) is skipped or reordered,
  # so the legacy regular file is migrated here too, defensively, rather than
  # relying solely on the sweep.
  _trw_degraded_marker_dir_ready "$_tcdl_path" 2>/dev/null || true
  rm -f "$_tcdl_path" 2>/dev/null || true
  return 0
}

# trw_degraded_latched: 0 when the offline block has already been emitted for
# this session. Consulted by the FR07 framework directive, which must stay
# suppressed for as long as the verdict stands, and by the FR02 emitter, which
# prints exactly once per session.
# Args: $1=optional payload session id. PRD-FIX-128-FR03: only THIS session's
# latch is consulted. An UNRESOLVABLE latch state returns non-zero, i.e. "not
# latched", which resolves to "emit" -- the direction PRD-CORE-247 chose for the
# framework directive, so no session is silently deprived of it by a peer.
trw_degraded_latched() {
  _tdl_path=$(trw_degraded_marker_path latch "${1:-}") || return 1
  [ -f "$_tdl_path" ]
}

# trw_bump_session_prompt_index: Increment and print this session's prompt index.
# Prints 0 when the marker is absent or malformed, which fails the FR01 prompt
# condition and so resolves to "surface present".
# Args: $1=optional payload session id. PRD-FIX-128-FR02: the counter is this
# session's, not the project's. Sharing one file made it a PROJECT prompt count
# (observed at 50), so the completed-second-prompt guard -- the discriminating
# signal of the whole verdict -- was satisfied for every new session on its
# first prompt.
trw_bump_session_prompt_index() {
  _tbspi_path=$(trw_degraded_marker_path epoch "${1:-}") || { printf '0'; return; }
  [ -r "$_tbspi_path" ] || { printf '0'; return; }
  _tbspi_ts=$(sed -n '1p' "$_tbspi_path" 2>/dev/null) || { printf '0'; return; }
  _tbspi_idx=$(sed -n '2p' "$_tbspi_path" 2>/dev/null) || _tbspi_idx=""
  case "$_tbspi_idx" in
    '' | *[!0-9]*) printf '0'; return ;;
  esac
  _tbspi_next=$((_tbspi_idx + 1))
  printf '%s\n%s\n' "$_tbspi_ts" "$_tbspi_next" > "$_tbspi_path" 2>/dev/null || { printf '0'; return; }
  printf '%s' "$_tbspi_next"
}

# trw_session_epoch_ts: Print the epoch timestamp, or nothing if unusable.
# The SHAPE is validated, not merely read: a marker whose first line is not an
# ISO-8601 UTC prefix cannot be compared lexicographically against a row `ts`,
# so it is rejected rather than trusted.
# Args: $1=optional payload session id (PRD-FIX-128-FR02).
trw_session_epoch_ts() {
  _tset_path=$(trw_degraded_marker_path epoch "${1:-}") || return 1
  [ -r "$_tset_path" ] || return 1
  _tset_ts=$(sed -n '1p' "$_tset_path" 2>/dev/null) || return 1
  case "$_tset_ts" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]) ;;
    *) return 1 ;;
  esac
  printf '%s' "$_tset_ts"
}

# _trw_scan_log_for_trw_call: scan ONE event log for a trw_ invocation.
#
# Returns 0 when $1 holds a `tool_invocation` row whose `tool_name` begins `trw_`
# with a `ts` at or after $2 -- and ALSO 0 when $1 cannot be read as an event
# stream at all, because "we could not read the log" is not evidence of silence.
# Returns 1 only in the one case that IS evidence: a readable, recognisable tail
# carrying no qualifying row.
#
# Extracted from trw_observed_trw_tool_call by PRD-FIX-128-FR01 so the tail cap,
# the positive-evidence gate, and both parser arms exist once rather than once
# per log -- two copies is how the weaker one drifts.
#
# Recency is evaluated on the ROW's own `ts`, not the file mtime, for the reason
# has_recent_session_tool_deliver documents: the log is appended by every tool
# call, so its mtime is always fresh. ISO-8601 UTC strings compare correctly as
# plain strings, so no per-row date parsing is needed. Cost is a bounded tail
# capped by $TRW_SESSION_EVENT_TAIL_LINES (default 500), never a full scan.
#
# Args: $1=log path, $2=cut timestamp.
# Return codes. PRD-FIX-128 external audit row 6 proposed a three-way split
# here ("confirmed no row" vs "could not tell") on the theory that the
# `&&  return 0` short circuit in trw_observed_trw_tool_call below skips the
# pinless log on a missing/unreadable owned-run log and so "inverts the
# detector on fresh runs". Verified against
# test_detector_reads_the_owned_run_event_log_before_the_pinless_fallback arm
# (d) and REFUTED: that test asserts, by design, that an absent/unreadable/
# malformed owned-run log must NOT be treated as silence even when the
# pinless log's only row is a non-trw_ tool call -- "we could not read the
# log is not evidence that no tool was called" (its own words). Because this
# function's fail-open code (0) already covers both "found" and
# "inconclusive", and the pinless call site's fail-open direction is
# identical, the short circuit is provably equivalent to always falling
# through: dropping the `&&` and unconditionally reading rc2 from the
# pinless call, this function returns 1 (not observed) only when EITHER
# scan is a confirmed 0-row read (rc=1 below) and the OTHER was too, and 0
# (present) whenever the owned scan is inconclusive, whatever the pinless
# scan finds. Consulting the pinless log first would not change that
# outcome, so the short circuit is a fork-count optimization (NFR01), not a
# defect. Left two-valued for that reason; a three-valued redesign is not
# needed here. See the CONFIRMED/REFUTED table in the PRD's
# "External audit findings addressed" section.
#   0 = a qualifying trw_ row was found, OR the log could not be used as
#       evidence (missing, unreadable, empty, or unparseable) -- fail-open
#   1 = the log was read; no qualifying row (a real negative)
_trw_scan_log_for_trw_call() {
  _tottc_events="$1"
  _tottc_since="$2"
  [ -f "$_tottc_events" ] || return 0
  [ -r "$_tottc_events" ] || return 0

  # PRD-FIX-128 external audit row 7: a typed, bounded tunable rather than a raw
  # env var read with no upper bound (models/config/_fields_degraded_mode.py).
  _tottc_tail=$(trw_degraded_event_tail_lines)

  # NFR02: "we read the log and saw no trw_ row" is evidence; "we could not read
  # the log" is not, and the two are otherwise indistinguishable — both parsers
  # below drop malformed lines rather than aborting, exactly as
  # has_recent_session_tool_deliver does. So require POSITIVE evidence that the
  # tail is a recognisable event stream before treating silence as absence. An
  # empty, truncated, or wholly-corrupt tail is INCONCLUSIVE (rc=2), not a
  # confirmed negative and not a confirmed positive.
  # ONE bounded read per file, reused by both the evidence gate and the parser:
  # the pre-FR128 shape ran `tail` twice per log, and FR01 now scans up to two
  # logs, so a second tail would have doubled the fork count this hook pays on
  # every prompt (NFR01).
  _tottc_body=$(tail -n "$_tottc_tail" "$_tottc_events" 2>/dev/null) || _tottc_body=""
  [ -n "$_tottc_body" ] || return 0

  _tottc_rows=$(printf '%s\n' "$_tottc_body" \
    | grep -c '"event"[[:space:]]*:' 2>/dev/null) || _tottc_rows=0
  case "$_tottc_rows" in
    '' | *[!0-9]*) _tottc_rows=0 ;;
  esac
  [ "$_tottc_rows" -gt 0 ] 2>/dev/null || return 0

  if command -v jq >/dev/null 2>&1; then
    printf '%s\n' "$_tottc_body" | jq -e -R --arg cut "$_tottc_since" '
        (fromjson? // empty)
        | select(.event == "tool_invocation" and ((.tool_name // "") | tostring | startswith("trw_")))
        | select(((.ts // "") | tostring)[0:19] >= $cut)
      ' >/dev/null 2>&1 && return 0
    return 1
  fi

  # Fallback path when jq is absent (PRD-FIX-128 external audit row 9): a real
  # JSON parser, matching the precedent already set by resolve_owned_run and
  # _trw_pin_rows, rather than the previous unanchored `awk` text match, which
  # matched "event"/"tool_name" substrings anywhere on the line (a false
  # positive on a non-trw_ tool call whose ARGUMENTS happened to contain those
  # strings) and extracted whichever `"ts"` occurred FIRST on the line rather
  # than the row's own timestamp (a false negative/positive on a row with more
  # than one `"ts"` key, e.g. inside a nested payload).
  if command -v python3 >/dev/null 2>&1; then
    printf '%s\n' "$_tottc_body" | python3 -c '
import json
import sys

cut = sys.argv[1]
found = False
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        row = json.loads(line)
    except ValueError:
        continue
    if not isinstance(row, dict):
        continue
    if row.get("event") != "tool_invocation":
        continue
    tool_name = row.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name.startswith("trw_"):
        continue
    ts = row.get("ts")
    if not isinstance(ts, str):
        continue
    if ts[:19] >= cut:
        found = True
        break
sys.exit(0 if found else 1)
' "$_tottc_since" >/dev/null 2>&1 && return 0
    return 1
  fi

  # Neither jq nor python3 is available: no parser here can tell a top-level
  # field from payload text, so this is inconclusive, not a confirmed negative.
  return 2
}

# trw_observed_trw_tool_call: 0 when THIS SESSION's tool trace shows a trw_
# invocation at or after $1 (its own epoch timestamp).
#
# PRD-FIX-128-FR01. The producer settled this long ago and the consumer never
# followed: tools/telemetry.py writes a tool_invocation row into the PINNED
# RUN's meta/events.jsonl and returns; only a session with no pinned run reaches
# the pinless .trw/context/session-events.jsonl. The two sinks are mutually
# exclusive by construction, and this function used to read the fallback one
# ONLY -- so every session that owned a run, which is exactly every session
# doing tracked work, was judged silent no matter how many tools it called.
#
# Order matters and is deliberate: the owned run first, the pinless log second.
# The pinless log is still scanned because a tool call made BEFORE the run was
# pinned lands there, and ignoring it would reintroduce a false positive in a
# session's first seconds. A qualifying row in EITHER file means the surface is
# present.
#
# Fail-open, per file: an absent pin store, an unresolvable key, a dangling run
# path, or an unreadable run event log each resolve to "present" rather than to
# silence -- see _trw_scan_log_for_trw_call and the section header.
#
# Args: $1=epoch timestamp, $2=optional payload session id.
trw_observed_trw_tool_call() {
  _tottc_cut="$1"
  [ -n "$_tottc_cut" ] || return 0
  _tottc_root="$(get_repo_root 2>/dev/null)" || return 0

  _tottc_run=$(resolve_owned_run "${2:-}" 2>/dev/null) || _tottc_run=""
  # Publish what was scanned so the emitter names THIS resolution rather than
  # repeating it: a second, independent lookup could disagree with the one the
  # verdict was actually computed from (FR05), and it costs a second pin-store
  # read on a hook that runs on every prompt (NFR01).
  _TRW_DEGRADED_SCANNED_RUN="$_tottc_run"
  # PRD-FIX-128 external audit row 6 proposed replacing this `&&` short circuit
  # with an explicit fall-through, on the theory that a missing/unreadable
  # owned-run log resolving straight to "found" (0) without consulting the
  # pinless log could mask a genuinely degraded session. REFUTED against
  # test_detector_reads_the_owned_run_event_log_before_the_pinless_fallback
  # arm (d): _trw_scan_log_for_trw_call's code 0 already covers BOTH "found"
  # and "could not be used as evidence", and the pinless call site shares the
  # identical fail-open direction, so this returns 1 (not observed) only when
  # BOTH calls confirm a readable log with no qualifying row -- exactly what
  # that test requires ("we could not read the log is not evidence that no
  # tool was called"). Falling through unconditionally would not change any
  # verdict, only add a fork on the path that matters least for NFR01 (an
  # owned run already found evidence). Left as `&&` for that reason.
  if [ -n "$_tottc_run" ]; then
    _trw_scan_log_for_trw_call "${_tottc_run}meta/events.jsonl" "$_tottc_cut" && return 0
  fi
  _trw_scan_log_for_trw_call "$_tottc_root/.trw/context/session-events.jsonl" "$_tottc_cut"
}

# trw_degraded_mode_detected: 0 when ALL THREE FR01 conditions hold.
#
# One condition is not sufficient evidence. The observed client gave up after
# 120000 ms, so any elapsed-time window shorter than that fires on healthy
# sessions; and elapsed time alone fires on a session whose first turn is a long
# read. A COMPLETED second prompt with no TRW call is the discriminating signal.
#
# Args: $1=this session's prompt index (from trw_bump_session_prompt_index),
#       $2=optional payload session id (PRD-FIX-128-FR06).
trw_degraded_mode_detected() {
  _tdmd_prompts="${1:-0}"
  _tdmd_key="${2:-}"

  # PRD-FIX-128-FR04: identity FIRST. Both halves of this verdict are
  # session-scoped claims -- "since THIS session began" and "no trw_ call by
  # THIS session" -- so a hook that cannot say which session it is serving has
  # nothing it can substantiate, and the subsystem's fail-open direction is
  # "the surface is present". Silence toward the AGENT is therefore correct;
  # silence toward the OPERATOR is not, because a client-profile regression
  # that stopped publishing a session variable would darken the whole detector
  # with no symptom at all. One record per prompt on an unidentified client is
  # expected and benign -- the monitored quantity is its volume.
  if ! trw_degraded_marker_path epoch "$_tdmd_key" >/dev/null 2>&1; then
    if trw_pin_key "$_tdmd_key" >/dev/null 2>&1; then
      _tdmd_reason="bad_key_shape"
    else
      _tdmd_reason="no_identity"
    fi
    log_hook_execution "UserPromptSubmit" "degraded_identity_unresolved" "0" "reason=$_tdmd_reason"
    return 1
  fi

  case "$_tdmd_prompts" in
    '' | *[!0-9]*) return 1 ;;
  esac
  [ "$_tdmd_prompts" -ge "$(trw_degraded_min_prompts)" ] 2>/dev/null || return 1

  _tdmd_epoch=$(trw_session_epoch_ts "$_tdmd_key") || return 1
  [ -n "$_tdmd_epoch" ] || return 1

  _tdmd_grace=$(trw_degraded_grace_seconds)
  _tdmd_cut=$(date -u -d "$_tdmd_grace seconds ago" '+%Y-%m-%dT%H:%M:%S' 2>/dev/null) || _tdmd_cut=""
  if [ -z "$_tdmd_cut" ]; then
    _tdmd_cut=$(date -u -v-"${_tdmd_grace}"S '+%Y-%m-%dT%H:%M:%S' 2>/dev/null) || _tdmd_cut=""
  fi
  # Without a usable date(1) we cannot bound elapsed time, so we cannot claim
  # the surface is absent.
  [ -n "$_tdmd_cut" ] || return 1
  [ "$_tdmd_epoch" \< "$_tdmd_cut" ] || return 1

  # Last, because it is the most expensive: a trw_ invocation newer than the
  # epoch means the surface is present, whatever the clock says.
  trw_observed_trw_tool_call "$_tdmd_epoch" "$_tdmd_key" && return 1
  return 0
}

# trw_emit_offline_protocol_block: Print the FR02 protocol and latch it.
#
# Names a concrete substitute for every obligation the RIGID line labels
# unconditional, so an agent that has lost the surface is told what to do instead
# of choosing between ignoring a rule, halting, or improvising. The build-check
# substitute is a written artifact rather than a claim, because the offline path
# has no gate to evaluate: recording the command and its exit code in reports/
# produces the same evidence a later reviewer needs, in the place trw_build_check
# results are read from.
#
# Every interpolated value — the epoch timestamp and the scanned run path —
# passes through _sanitize_context_text first (NFR03).
#
# Args: $1=this session's epoch timestamp, $2=optional payload session id.
trw_emit_offline_protocol_block() {
  _teopb_root="$(get_repo_root 2>/dev/null)" || return 0
  _teopb_since=$(_sanitize_context_text "${1:-}")
  # PRD-FIX-132: name the run this session owns on every run-scoped line. The
  # offline CLI refuses to select a run it cannot prove belongs to the caller,
  # so a block that printed a bare "trw-mcp local checkpoint --message MSG" was
  # printing a command that would fail -- and, before the CLI was fixed, one
  # that wrote into whichever run another agent had touched most recently.
  # Ownership comes from the pin (resolve_owned_run), never from recency, and
  # resolving it is read-only.
  # PRD-FIX-128-FR06: pass the payload identity through. This was the ONLY one
  # of the eight shipped resolve_owned_run call sites that dropped the fallback
  # argument, so on a client exporting no session variable the emitter could
  # never name a run -- it silently took the placeholder branch instead.
  #
  # When the detector just ran it already resolved this, so its answer is reused
  # rather than recomputed. Reached directly (the offline-CLI tests do), the
  # variable is unset and the lookup happens here.
  if [ -n "${_TRW_DEGRADED_SCANNED_RUN+set}" ]; then
    _teopb_run="$_TRW_DEGRADED_SCANNED_RUN"
  else
    _teopb_run=$(resolve_owned_run "${2:-}" 2>/dev/null) || _teopb_run=""
  fi
  if [ -n "$_teopb_run" ]; then
    _teopb_rp=" --run-path $(_sanitize_context_text "${_teopb_run%/}")"
  else
    # Not a resolved path and not a guess: a placeholder the notice below
    # explains how to fill. Printing the flag with a placeholder keeps the
    # command correct-by-construction instead of correct-only-by-luck.
    _teopb_rp=" --run-path DIR"
  fi
  echo "TRW DEGRADED MODE: no trw_ tool call has been observed since this session began${_teopb_since:+ at $_teopb_since}."
  echo "  This is INFERRED from that absence, not observed: no hook can ask the client whether MCP"
  echo "  attached. If the trw_ tools are in fact available, use them and ignore the rest of this."
  echo "  If they are not, every obligation below still binds — RIGID names an OBLIGATION, not a"
  echo "  tool call — so use the offline substitute."
  # PRD-FIX-128-FR05: name the evidence. A verdict that names the log it was
  # computed from is auditable; one that does not is an assertion. The path is
  # sanitized exactly as the timestamp is -- it originates in pins.json, which
  # is machine-written but process-writable.
  if [ -n "$_teopb_run" ]; then
    echo "  EVIDENCE: scanned this session's own pinned run event log at" \
      "$(_sanitize_context_text "${_teopb_run%/}/meta/events.jsonl"), then the pinless session log."
  else
    echo "  EVIDENCE: no run is pinned to this session, so ONLY the pinless session log" \
      "(.trw/context/session-events.jsonl) was scanned."
  fi
  echo ""
  echo "  trw_session_start -> trw-mcp local status$_teopb_rp        (run state)"
  echo "                      trw-mcp local recall --query \"<domain>\"   (prior learnings)"
  echo "  trw_init          -> trw-mcp local init --task NAME"
  echo "  trw_checkpoint    -> trw-mcp local checkpoint --message MSG$_teopb_rp"
  echo "  trw_learn         -> trw-mcp local learn --summary S --detail D --tag T"
  echo "  trw_recall        -> trw-mcp local recall --query Q"
  echo "  trw_build_check   -> run the project-native check yourself, then write the EXACT command"
  echo "                      string and its integer exit code into the active run's reports/ directory"
  echo "  trw_deliver       -> trw-mcp local deliver --message MSG$_teopb_rp, which records gate_evaluated: false."
  echo "                      That is an UNGATED delivery: CONSTITUTION 1.a still binds until evidence exists."
  echo "  Feedback          -> trw-mcp local feedback --category C --subject S --message M"
  echo ""
  if [ -n "$_teopb_run" ]; then
    echo "  RUN IDENTITY: resolved from this session's pin and already filled in above."
  else
    echo "  RUN IDENTITY UNKNOWN: no pin for this session, so DIR above is a placeholder, not a path."
    echo "  Run the local init line first and substitute the run path it prints. The CLI will refuse"
    echo "  to choose a run for you -- choosing by recency is how a checkpoint lands in another"
    echo "  session's run -- so a run-scoped command without a real DIR writes nothing."
  fi
  echo ""
  echo "  RECONCILIATION: writes made here are marked (source_identity=local_cli plus a"
  echo "  trw-reconcile-pending tag) and the next successful trw_session_start reports them back."
  # PRD-FIX-128-FR03: latch THIS session only. With no resolvable identity there
  # is nothing to latch, and the block above was not emitted either.
  _teopb_latch=$(trw_degraded_marker_path latch "${2:-}") || return 0
  _trw_degraded_marker_dir_ready "$_teopb_latch" || return 0
  : > "$_teopb_latch" 2>/dev/null || true
}

# trw_degenerate_result_setting: resolve one PRD-CORE-250-FR10 tunable.
#
# ONE accessor for all five, so the precedence rule and the malformed-value
# fallback are defined once rather than five times in the adapter. That is what
# lets post-tool-degenerate-result.sh carry no numeric literal of its own — the
# property FR10 pins with `grep_absent: "=50"`.
#
# Args: $1=short key — cooldown_calls | max_read_bytes | deadline_ms |
#       truncation_markers | freshness_commands.
# Prints: one integer for the first three; one item per LINE for the last two.
# Returns 1 (printing nothing) for an unknown key.
#
# Precedence, first non-empty wins, following trw_stop_deliver_window_min:
#   1. $TRW_DEGENERATE_RESULT_<KEY>                    (env, per session; scalars only)
#   2. <field>: in .trw/config.yaml                    (project)
#   3. the default below                               (matches the Pydantic field)
#
# The Pydantic field in models/config/_fields_ceremony.py is the SOURCE OF TRUTH
# for every default here; a shell hook cannot import it, so the two copies are
# pinned equal by test_degenerate_result_adapter.py::test_shell_defaults_match_
# the_typed_fields rather than left to drift.
#
# A non-numeric scalar falls back to the default rather than to 0: a mistyped
# cooldown must not silently disable the advisory, and a mistyped deadline must
# not silently make every invocation time out.
trw_degenerate_result_setting() {
  case "${1:-}" in
    cooldown_calls) _tdrs_field='degenerate_result_cooldown_calls'; _tdrs_kind=int
      _tdrs_default='20'; _tdrs_min='1'; _tdrs_max='200'
      _tdrs_env="${TRW_DEGENERATE_RESULT_COOLDOWN_CALLS:-}" ;;
    max_read_bytes) _tdrs_field='degenerate_result_max_read_bytes'; _tdrs_kind=int
      _tdrs_default='65536'; _tdrs_min='1024'; _tdrs_max='1048576'
      _tdrs_env="${TRW_DEGENERATE_RESULT_MAX_READ_BYTES:-}" ;;
    deadline_ms) _tdrs_field='degenerate_result_deadline_ms'; _tdrs_kind=int
      _tdrs_default='50'; _tdrs_min='5'; _tdrs_max='500'
      _tdrs_env="${TRW_DEGENERATE_RESULT_DEADLINE_MS:-}" ;;
    truncation_markers) _tdrs_field='degenerate_result_truncation_markers'; _tdrs_kind=list
      _tdrs_default='more lines]
Output too large'; _tdrs_env='' ;;
    freshness_commands) _tdrs_field='degenerate_result_freshness_commands'; _tdrs_kind=list
      _tdrs_default='git log
date
ls -l
stat
curl
gh api
gh run list'; _tdrs_env='' ;;
    *) return 1 ;;
  esac

  if [ -n "$_tdrs_env" ]; then
    _tdrs_val="$_tdrs_env"
  else
    _tdrs_cfg="$(get_repo_root 2>/dev/null)/.trw/config.yaml"
    _tdrs_val=''
    if [ -f "$_tdrs_cfg" ]; then
      if [ "$_tdrs_kind" = int ]; then
        _tdrs_val=$(grep "^$_tdrs_field:" "$_tdrs_cfg" 2>/dev/null | head -1 \
          | sed "s/^$_tdrs_field:[[:space:]]*//" | tr -d "'\"" | tr -d '[:space:]')
      else
        # Both YAML sequence spellings, because the config is hand-editable and
        # ruamel round-trips whichever the user wrote: a flow list on the key's
        # own line, or a block list in the lines after it.
        _tdrs_val=$(awk -v key="$_tdrs_field" '
          !seen && index($0, key ":") == 1 {
            rest = substr($0, length(key) + 2)
            gsub(/^[ \t]+|[ \t]+$/, "", rest)
            seen = 1
            if (rest ~ /^\[.*\]$/) {
              inner = substr(rest, 2, length(rest) - 2)
              n = split(inner, parts, ",")
              for (i = 1; i <= n; i++) {
                v = parts[i]
                gsub(/^[ \t\042\047]+|[ \t\042\047]+$/, "", v)
                if (v != "") print v
              }
              exit
            }
            block = 1
            next
          }
          block && /^[ \t]*-[ \t]*/ {
            v = $0
            sub(/^[ \t]*-[ \t]*/, "", v)
            gsub(/^[\042\047]+|[\042\047]+$/, "", v)
            if (v != "") print v
            next
          }
          block { exit }
        ' "$_tdrs_cfg" 2>/dev/null)
      fi
    fi
  fi

  if [ "$_tdrs_kind" = int ]; then
    case "$_tdrs_val" in
      '' | *[!0-9]*) printf '%s' "$_tdrs_default"; return 0 ;;
    esac
    # CLAMP to the Pydantic field's own ge/le. Pydantic validates
    # `.trw/config.yaml` only when the SERVER loads it; this accessor reads the
    # same file with grep and sed, from a hook that never imports the model, so
    # nothing between a hand-edited value and its consumer enforced the bound.
    # `degenerate_result_max_read_bytes: 999999999` therefore flowed straight
    # into `head -c` and defeated NFR03's cap — the review found it as a live
    # contradiction, not a hypothetical. Clamping rather than falling back to the
    # default is deliberate: an out-of-range value states an intent (bigger cap,
    # longer window) and the honest answer is the nearest value the contract
    # allows, whereas the default would silently ignore what the operator asked
    # for. `_tdrs_min`/`_tdrs_max` mirror the field declarations in
    # models/config/_fields_ceremony.py and are pinned equal to them by
    # test_degenerate_result_adapter.py::test_shell_bounds_match_the_typed_fields.
    if [ "$_tdrs_val" -lt "$_tdrs_min" ] 2>/dev/null; then
      printf '%s' "$_tdrs_min"
    elif [ "$_tdrs_val" -gt "$_tdrs_max" ] 2>/dev/null; then
      printf '%s' "$_tdrs_max"
    else
      printf '%s' "$_tdrs_val"
    fi
    return 0
  fi
  # An EMPTY configured list is a real setting, not a missing one — emptying
  # freshness_commands is the documented OQ-02 rollback for rule 3 — but the key
  # being absent altogether must still yield the default. `grep -q` on the key
  # tells the two apart.
  if [ -n "$_tdrs_val" ]; then
    printf '%s\n' "$_tdrs_val"
  elif [ -f "${_tdrs_cfg:-/nonexistent}" ] && grep -q "^$_tdrs_field:" "${_tdrs_cfg:-/nonexistent}" 2>/dev/null; then
    return 0
  else
    printf '%s\n' "$_tdrs_default"
  fi
}
