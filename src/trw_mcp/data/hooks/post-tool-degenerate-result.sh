#!/bin/sh
# PRD-CORE-250-FR06: PostToolUse advisory — an empty, truncated, or undated result
# does not distinguish "absent" from "could not look".
#
# Enforces the rule FRAMEWORK-CORE.md already states in prose: *absence of a
# measurement is not a measurement of absence*. The failure it addresses is not a
# tool failing — it is a tool succeeding with a result whose SHAPE cannot carry
# the conclusion the reader is about to draw from it. Three shapes qualify:
#
#   1. EMPTY      — the rendered result is empty or whitespace-only.
#   2. TRUNCATED  — it carries a marker from the configured truncation set.
#   3. UNDATED    — the tool is Bash, its command matches the configured
#                   freshness allowlist, and the output carries no YYYY-MM-DD or
#                   epoch-seconds token.
#
# Advisory ONLY. Never blocks: PostToolUse runs after the tool has already run,
# and the exit code is 0 on every path including every failure of this script.
#
# CHANNEL. The advisory goes to stdout as a PostToolUse `additionalContext`
# block, which is the model-visible, non-blocking feedback path for this event.
# `{"decision": "block", ...}` was the other candidate and was rejected: it
# renders as an error to the user for something that is not one. If the JSON
# cannot be written, the line goes to stderr instead — the channel
# stop-ceremony.sh uses for its at-ceiling advisory — and the exit code is still 0.
#
# BOUNDED, because an advisory the loop learns to ignore is worse than none. At
# most one advisory per `degenerate_result_cooldown_calls` MATCHING results per
# session. The fire rate is measured rather than assumed: replaying every
# recorded live tool result through this hook with the cooldown DISABLED holds
# well under the NFR06 budget of 5 per 100, so the cooldown is margin rather
# than the thing that makes the budget. Counts, N and date:
# .trw/compliance/degenerate-result-calibration.json
# (regenerate with scripts/measure_degenerate_result_calibration.py).
#
# jq IS REQUIRED, and its absence is silence rather than a second parser. All
# three rules are shape tests over a structured field, and a sed approximation of
# "render this JSON value to text" would be a DIFFERENT classifier wearing the
# same name — the two-path shape that has produced fail-open defects in this
# hooks directory before. NFR02 asks for exactly this: absent jq produces no
# advisory and exit 0.
#
# NFR03: no payload-derived text is ever interpolated into a command, an eval, or
# a filename, and the advisory line is a fixed constant — so a hostile tool
# output cannot inject content or ANSI escapes through it. The read is capped at
# `degenerate_result_max_read_bytes`.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

# PRD-CORE-149 FR05: a disabled hook must not consume stdin or emit anything.
if [ "${HOOKS_ENABLED:-true}" = "false" ]; then
  exit 0
fi

init_hook_timer

command -v jq >/dev/null 2>&1 || exit 0

# --- typed tunables, all through the one accessor (FR10) ---------------------
# No numeric literal for any of these appears below; the defaults live beside the
# Pydantic fields they mirror, in lib-trw.sh::trw_degenerate_result_setting.
_max_bytes=$(trw_degenerate_result_setting max_read_bytes) || exit 0
_deadline_ms=$(trw_degenerate_result_setting deadline_ms) || exit 0
_cooldown=$(trw_degenerate_result_setting cooldown_calls) || exit 0

# --- the self-imposed deadline (NFR01) ---------------------------------------
# `date +%s%N` is GNU/busybox, not POSIX. When it is unavailable the deadline is
# simply not enforced rather than treated as expired: the alternative would make
# this hook a permanent no-op on a platform where it works fine, and the read is
# byte-capped and the work is O(bytes) regardless.
_dr_now_ms() {
  _drn=$(date +%s%N 2>/dev/null) || _drn=''
  case "$_drn" in
    '' | *[!0-9]*) return 1 ;;
    *) printf '%s' $((_drn / 1000000)) ;;
  esac
}
_started_ms=$(_dr_now_ms) || _started_ms=''

_dr_past_deadline() {
  [ -n "$_started_ms" ] || return 1
  _drd_now=$(_dr_now_ms) || return 1
  [ $((_drd_now - _started_ms)) -ge "$_deadline_ms" ]
}

# --- the payload, read under the byte cap (NFR03) ----------------------------
# `head -c` is not POSIX, so its availability is probed rather than assumed; the
# fallback is `dd bs=1`, which is POSIX and correct, and whose extra syscalls the
# deadline above already bounds.
if printf '' | head -c 1 >/dev/null 2>&1; then
  _payload=$(head -c "$_max_bytes" 2>/dev/null) || exit 0
else
  _payload=$(dd bs=1 count="$_max_bytes" 2>/dev/null) || exit 0
fi
[ -n "$_payload" ] || exit 0

# Malformed JSON is silence, not an error (NFR02).
printf '%s' "$_payload" | jq -e . >/dev/null 2>&1 || exit 0

# Render tool_response to text WHATEVER its shape: a string renders to itself, an
# object to its string leaves. That is deliberately generic — clients disagree
# about the shape (`{"stdout":…,"stderr":…}` for one tool, `{"file":{…}}` for
# another) and hard-coding one client's field names would make rule 1 answer
# "not empty" for every result of every other shape.
_rendered=$(printf '%s' "$_payload" | jq -r '.tool_response // "" | [.. | strings] | join("\n")' 2>/dev/null) || exit 0
_tool=$(printf '%s' "$_payload" | jq -r '.tool_name // ""' 2>/dev/null) || _tool=''
_command=$(printf '%s' "$_payload" | jq -r '.tool_input.command // ""' 2>/dev/null) || _command=''

_dr_past_deadline && exit 0

# --- rule 1: empty ------------------------------------------------------------
_shape=''
if [ -z "$(printf '%s' "$_rendered" | tr -d '[:space:]')" ]; then
  _shape='empty'
fi

# --- rule 2: truncated --------------------------------------------------------
# `grep -F` against configured LITERALS. Never a regex, never interpolated into a
# command: the marker reaches grep as an argument and the payload only ever
# reaches it on stdin.
if [ -z "$_shape" ]; then
  _hit=$(trw_degenerate_result_setting truncation_markers | while IFS= read -r _marker; do
    [ -n "$_marker" ] || continue
    if printf '%s' "$_rendered" | grep -qF -- "$_marker" 2>/dev/null; then
      printf 'y'
      break
    fi
  done)
  if [ "$_hit" = "y" ]; then
    _shape='truncated'
  fi
fi

# --- rule 3: undated, on a freshness-sensitive read ---------------------------
# The noisiest of the three, so it is gated on an explicit command allowlist
# matched as a FIRST-LINE PREFIX rather than on a heuristic. Prefix vs substring
# is not a style choice: on the same seven entries, substring matching blew the
# NFR06 budget several times over and prefix matching holds it. Both figures are
# in .trw/compliance/degenerate-result-calibration.json.
if [ -z "$_shape" ] && [ "$_tool" = "Bash" ] && [ -n "$_command" ]; then
  _first_line=$(printf '%s' "$_command" | head -1)
  _eligible=0
  for _prefix in $(trw_degenerate_result_setting freshness_commands | tr ' ' '\037'); do
    _prefix=$(printf '%s' "$_prefix" | tr '\037' ' ')
    case "$_first_line" in
      "$_prefix" | "$_prefix "*) _eligible=1 ;;
    esac
  done
  # `(^|[^0-9])` rather than `[^0-9]`: the bare character class REQUIRES a
  # preceding character, so an epoch at offset 0 — `1788469550 ...`, which is
  # exactly what `date +%s` and a bare timestamp column produce — did not match
  # and the result was misread as undated. A left boundary must accept the start
  # of the string.
  if [ "$_eligible" = "1" ] && ! printf '%s' "$_rendered" \
    | grep -qE '[0-9]{4}-[0-9]{2}-[0-9]{2}|(^|[^0-9])1[6-9][0-9]{8}([^0-9]|$)'; then
    _shape='undated'
  fi
fi

[ -n "$_shape" ] || exit 0
_dr_past_deadline && exit 0

# --- the cooldown, keyed by THIS session (NFR05) ------------------------------
# Two sessions in one repository keep independent counters, because the state
# path carries trw_pin_key. A session with no identity gets its own "unpinned"
# slot rather than sharing another session's. The key is sanitized to a safe
# filename charset before it is used as one — it is client-supplied.
_root=$(get_repo_root 2>/dev/null) || exit 0
[ -d "$_root/.trw" ] || exit 0
_key=$(trw_pin_key "$(printf '%s' "$_payload" | jq -r '.session_id // ""' 2>/dev/null)") || _key='unpinned'
[ -n "$_key" ] || _key='unpinned'
_safe_key=$(printf '%s' "$_key" | tr -c 'A-Za-z0-9._-' '_' | cut -c1-64)
_state_dir="$_root/.trw/context"
_state="$_state_dir/degenerate-advisory-$_safe_key.state"

# Containment, mirroring resolve_owned_run: a state path that escapes the project
# root is refused rather than written.
case "$_state" in
  "$_root"/*) ;;
  *) exit 0 ;;
esac
[ -d "$_state_dir" ] || mkdir -p "$_state_dir" 2>/dev/null || exit 0

_remaining=$(cat "$_state" 2>/dev/null) || _remaining=''
case "$_remaining" in
  '' | *[!0-9]*) _remaining=0 ;;
esac

_dr_write_state() {
  # write-temp-then-mv, so an interleaved write from a concurrent session can
  # never leave a half-written counter behind (NFR05).
  _drw_tmp="$_state.$$"
  printf '%s\n' "$1" > "$_drw_tmp" 2>/dev/null || return 0
  mv -f "$_drw_tmp" "$_state" 2>/dev/null || rm -f "$_drw_tmp" 2>/dev/null || true
}

if [ "$_remaining" -gt 0 ]; then
  _dr_write_state $((_remaining - 1))
  log_hook_execution "PostToolUse:degenerate-result" "$_shape" "0" "advisory=suppressed cooldown=$_remaining"
  exit 0
fi

_dr_past_deadline && exit 0
_dr_write_state "$_cooldown"

# --- the advisory: one fixed line, no payload-derived text --------------------
# Single quotes inside, deliberately: this string is embedded verbatim in the
# JSON below, and the double-quoted spelling produced a payload the client could
# not parse — so the advisory reached nobody while the hook reported success.
# A fixed constant needs no escaper; it needs to already be valid.
_line="This result does not distinguish 'absent' from 'could not look' — confirm before concluding."
if ! printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"%s"}}\n' "$_line"; then
  printf '%s\n' "$_line" >&2 || true
fi
log_hook_execution "PostToolUse:degenerate-result" "$_shape" "0" "advisory=emitted"
exit 0
