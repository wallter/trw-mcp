#!/bin/sh
# PRD-INFRA-002-FR12: PostToolUse hook — auto-log file_modified events.
# Fires after Write/Edit tool completions.
#
# Reads JSON from stdin (Claude Code PostToolUse payload).
# Appends a file_modified event to the active run's events.jsonl, or — when no run
# can be resolved — to the session-scoped .trw/context/session-events.jsonl so an
# UNPINNED session still leaves change evidence the deliver gate can read
# (PRD-FIX-140-FR04).
#
# Exit code 0 always (fail-open, async hook).
#
# Performance: ~75ms avg latency (benchmarked 2026-03-29, 5 runs).
# Primary cost used to be find_active_run() scanning .trw/runs/ for run.yaml
# files; since PRD-FIX-118 an identified session resolves its run with a single
# pins.json lookup and that scan is reached only when identity is unknowable.
# Dependencies: POSIX shell. jq optional (used for file_path extraction).

set -e
trap 'exit 0' EXIT

# Source shared utilities
_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

# Read JSON payload from stdin
_payload=$(cat) || exit 0

# jq only: no shell JSON parser (T29). Without jq this hook cannot tell which
# tool ran, which file it touched, or whose session it was. It records exactly
# that -- change evidence unknown -- in the checkout's session stream, and the
# deliver gate reads the row as uncomputable (it blocks) rather than as zero.
if ! command -v jq >/dev/null 2>&1; then
  _unk_root="$(get_repo_root)" || exit 0
  # A fresh checkout has no .trw/context yet; skipping the row there would read
  # as "no changes" again, so the directory is created, not required.
  mkdir -p "$_unk_root/.trw/context" 2>/dev/null || true
  printf '{"ts":"%s","event":"change_evidence_unknown","reason":"jq_unavailable"}\n' \
    "$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || printf 'unknown')" \
    >>"$_unk_root/.trw/context/session-events.jsonl" 2>/dev/null || true
  log_hook_execution "PostToolUse" "unknown" "0" "jq_unavailable=1"
  exit 0
fi
_file_path=$(printf '%s' "$_payload" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || _file_path=""
_tool_name=$(_json_str_field "$_payload" tool_name) || _tool_name=""
_host_session_id=$(_json_str_field "$_payload" session_id) || _host_session_id=""
_session_id=${TRW_SESSION_ID:-}

# Nothing to log if no file_path
[ -n "$_file_path" ] || exit 0

# PRD-FIX-118 FR02/FR03: resolve THIS session's own run through the shared
# ownership primitive. The pins.json lookup + project-root containment check this
# hook used to inline verbatim now live once in lib-trw.sh::resolve_owned_run, so
# every hook answers "which run is mine?" the same way.
#
# The stdin payload's session_id is passed as the fallback key for the case where
# hook-env.sh predates FR01 and TRW_SESSION_ID is unset: for a client whose pin
# key IS its host session id that fallback resolves correctly, and for any other
# client it simply fails to match, which is the honest unowned outcome.
_pin_key=$(trw_pin_key "${_host_session_id:-}") || _pin_key=""

# PRD-FIX-140-FR04: an UNPINNED session still has to leave change evidence.
# Without this branch a session that never ran trw_init recorded NOTHING when it
# edited files, so the deliver gate's change-evidence clause measured 0 for it and
# an unpinned delivery could never block on "this session changed code" — the
# client-side hook used to paper over that by blocking every missing build record.
# The record goes to the session-scoped stream write_session_deliver_marker
# already uses, in the same flat shape, so it survives without a run directory.
# Bounded and fail-open: three edit tools only, one line, a length-capped path,
# skipped once the stream is large, and never a non-zero exit.
_append_unpinned_change() {
  case "$_tool_name" in
    Write|Edit|MultiEdit|NotebookEdit) ;;
    *) return 0 ;;
  esac
  _uc_key="${_session_id:-$_pin_key}"
  [ -n "$_uc_key" ] || return 0
  _uc_root="$(get_repo_root)" || return 0
  _uc_events="$_uc_root/.trw/context/session-events.jsonl"
  mkdir -p "$_uc_root/.trw/context" 2>/dev/null || return 0
  if [ -f "$_uc_events" ]; then
    _uc_size=$(wc -c <"$_uc_events" 2>/dev/null | tr -d ' ') || _uc_size=0
    case "$_uc_size" in
      ''|*[!0-9]*) _uc_size=0 ;;
    esac
    [ "$_uc_size" -lt 8388608 ] || return 0
  fi
  # Repo-relative, so the same edit recorded absolutely and relatively dedupes to
  # one path on the reader side (tools/_delivery_event_checks.py).
  _uc_file="${_file_path#"$_uc_root"/}"
  _uc_file=$(printf '%s' "$_uc_file" | cut -c1-500)
  printf '{"ts":"%s","event":"file_modified","tool":"%s","file":"%s","session_id":"%s","pinned":false}\n' \
    "$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || printf 'unknown')" \
    "$(_json_escape "$_tool_name")" "$(_json_escape "$_uc_file")" "$(_json_escape "$_uc_key")" \
    >>"$_uc_events" 2>/dev/null || return 0
  log_hook_execution "PostToolUse" "$_tool_name" "0:unpinned-change"
  return 0
}

_run_dir=$(resolve_owned_run "${_host_session_id:-}") || _run_dir=""
if [ -z "$_run_dir" ]; then
  if [ -n "$_pin_key" ]; then
    # Never attribute an identified session's edit to another session's newest run.
    _append_unpinned_change
    exit 0
  fi
  # Identity unknowable — legacy single-instance fallback (FR03 permits this
  # only on this branch).
  _run_dir=$(find_active_run) || { _append_unpinned_change; exit 0; }
fi
if [ -z "$_run_dir" ]; then
  _append_unpinned_change
  exit 0
fi

_events_path="${_run_dir}meta/events.jsonl"

# Ensure events directory exists
[ -d "$(dirname "$_events_path")" ] || exit 0

# Append file_modified event.
# SECURITY: tool_name / file_path are attacker-influenceable — JSON-escape both
# before embedding so a value containing a quote/backslash/newline cannot break
# out of the JSON string and inject extra fields or split the JSONL line.
_tool_name_esc="$(_json_escape "$_tool_name")"
_file_path_esc="$(_json_escape "$_file_path")"
_session_id_esc="$(_json_escape "$_session_id")"
_host_session_id_esc="$(_json_escape "${_host_session_id:-}")"
append_event "$_events_path" "file_modified" "\"tool\":\"$_tool_name_esc\",\"file\":\"$_file_path_esc\",\"session_id\":\"$_session_id_esc\",\"host_session_id\":\"$_host_session_id_esc\""

log_hook_execution "PostToolUse" "$_tool_name" "0"

exit 0
