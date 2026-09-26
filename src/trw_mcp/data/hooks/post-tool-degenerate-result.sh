#!/bin/sh
# PRD-CORE-250-FR06: PostToolUse advisory — an empty, truncated, or undated result
# does not distinguish "absent" from "could not look".
#
# Enforces the rule FRAMEWORK.md already states in prose: *absence of a
# measurement is not a measurement of absence*. The failure it addresses is not a
# tool failing — it is a tool succeeding with a result whose SHAPE cannot carry
# the conclusion the reader is about to draw from it. Four shapes qualify:
#
#   1. EMPTY      — the rendered result is empty or whitespace-only.
#   2. TRUNCATED  — it carries a marker from the configured truncation set.
#   3. UNDATED    — the tool is Bash, its command matches the configured
#                   freshness allowlist, and the output carries no YYYY-MM-DD or
#                   epoch-seconds token.
#   4. OVERSIZED  — (PRD-INFRA-194-FR04) the rendered result's byte length
#                   exceeds `tool_output_size_warning_bytes`, OR the stdin read
#                   itself hit `degenerate_result_max_read_bytes` (the payload
#                   is too large even to classify) — see the OVERSIZED section
#                   below for why the second case cannot go through jq.
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
# TWO SIGNALS, ONE CALL: rules 1-3 (shape) and rule 4 (size) are independent —
# each has its own per-session cooldown state file — but a single PostToolUse
# response can only carry one `additionalContext` string. When both fire on the
# same call this hook COMBINES them into one string joined by a single space
# rather than emitting two JSON objects (which is not a valid response) or
# dropping one. See the emission block near the end of this file.
#
# BOUNDED, because an advisory the loop learns to ignore is worse than none. At
# most one shape advisory per `degenerate_result_cooldown_calls` MATCHING results
# per session, and independently, at most one size advisory per the same
# `cooldown_calls` window per session. The fire rate is measured rather than
# assumed: replaying every recorded live tool result through this hook with the
# cooldown DISABLED holds well under the NFR06 budget of 5 per 100, so the
# cooldown is margin rather than the thing that makes the budget. Counts, N and
# date: .trw/compliance/degenerate-result-calibration.json
# (regenerate with scripts/measure_degenerate_result_calibration.py).
#
# jq IS REQUIRED, and its absence is silence rather than a second parser. All
# three shape rules are shape tests over a structured field, and a sed
# approximation of "render this JSON value to text" would be a DIFFERENT
# classifier wearing the same name — the two-path shape that has produced
# fail-open defects in this hooks directory before. NFR02 asks for exactly this:
# absent jq produces no advisory and exit 0. The size rule's OVERSIZED-BY-CAP
# branch is the one deliberate exception to "jq or nothing" — see below.
#
# NFR03: no payload-derived text is ever interpolated into a command, an eval, or
# a filename, and both advisory lines are fixed constants — so a hostile tool
# output cannot inject content or ANSI escapes through them. The read is capped
# at `degenerate_result_max_read_bytes`.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

command -v jq >/dev/null 2>&1 || exit 0

# --- typed tunables, all through the one accessor (FR10 + PRD-INFRA-194-FR04) -
# No numeric literal for any of these appears below; the defaults live beside the
# Pydantic fields they mirror, in lib-trw.sh::trw_degenerate_result_setting.
_max_bytes=$(trw_degenerate_result_setting max_read_bytes) || exit 0
_deadline_ms=$(trw_degenerate_result_setting deadline_ms) || exit 0
_cooldown=$(trw_degenerate_result_setting cooldown_calls) || exit 0
_size_threshold=$(trw_degenerate_result_setting size_warning_bytes) || exit 0

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

# --- shared helpers: state dir/key resolution, the cooldown gate, emission ----
# Refactored into functions so the shape family (rules 1-3) and the size family
# (rule 4) share ONE cooldown implementation instead of two copies that could
# drift. Both families use the same `cooldown_calls` value but a DIFFERENT state
# file, so their counters never interact (NFR05).
_dr_write_state() {
  # write-temp-then-mv via the shared _trw_safe_write (lib-trw.sh), so an
  # interleaved write from a concurrent session can never leave a
  # half-written counter behind (NFR05) AND a symlinked state path or
  # `.trw/context` ancestor cannot redirect the write (PRD-SEC/RC8). $1=path
  # $2=value. Returns 1 when the counter was NOT persisted; the caller
  # decides what that means.
  printf '%s\n' "$2" | _trw_safe_write "$1"
}

# _dr_prepare_dirs: sets $_root and $_state_dir. Returns 1 (nothing usable) if
# there is no project .trw directory to write cooldown state into.
_dr_prepare_dirs() {
  _root=$(get_repo_root 2>/dev/null) || return 1
  [ -d "$_root/.trw" ] || return 1
  _state_dir="$_root/.trw/context"
  [ -d "$_state_dir" ] || mkdir -p "$_state_dir" 2>/dev/null || return 1
  return 0
}

# _dr_safe_key: $1=raw session id (or empty). Prints a filename-safe key.
# Sanitized to a safe filename charset before use as one — it is client-supplied.
_dr_safe_key() {
  _dsk_key=$(trw_pin_key "$1") || _dsk_key='unpinned'
  [ -n "$_dsk_key" ] || _dsk_key='unpinned'
  printf '%s' "$_dsk_key" | tr -c 'A-Za-z0-9._-' '_' | cut -c1-64
}

# _dr_gate STATE_PATH COOLDOWN SHAPE_LABEL
# Returns 0 ("fire": writes COOLDOWN into STATE_PATH) or 1 ("suppressed":
# decrements the remaining counter and logs why). Refuses a STATE_PATH that
# escapes $_root, mirroring resolve_owned_run.
_dr_gate() {
  _drg_state="$1"; _drg_cooldown="$2"; _drg_shape="$3"
  case "$_drg_state" in
    "$_root"/*) ;;
    *) return 1 ;;
  esac
  _drg_remaining=$(_trw_safe_read "$_drg_state") || _drg_remaining=''
  case "$_drg_remaining" in
    '' | *[!0-9]*) _drg_remaining=0 ;;
  esac
  if [ "$_drg_remaining" -gt 0 ]; then
    _dr_write_state "$_drg_state" $((_drg_remaining - 1))
    log_hook_execution "PostToolUse:degenerate-result" "$_drg_shape" "0" "advisory=suppressed cooldown=$_drg_remaining"
    return 1
  fi
  # FAIL TOWARD SUPPRESSION: a cooldown that cannot be recorded cannot be
  # honoured, so firing here would repeat the advisory on EVERY call. An advisory
  # is optional; a flood of it is the harm the cooldown exists to prevent.
  if ! _dr_write_state "$_drg_state" "$_drg_cooldown"; then
    log_hook_execution "PostToolUse:degenerate-result" "$_drg_shape" "0" "advisory=suppressed state=unwritable"
    return 1
  fi
  return 0
}

# _dr_emit TEXT: the sanctioned channel — a fixed constant needs no escaper; it
# needs to already be valid. Single quotes inside the constants below,
# deliberately: the double-quoted spelling once produced a payload the client
# could not parse.
_dr_emit() {
  if ! printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"%s"}}\n' "$1"; then
    printf '%s\n' "$1" >&2 || true
  fi
}

_SIZE_LINE="This tool output is large (over the configured size threshold) — prefer narrower queries or summarise before reusing it."

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

# --- OVERSIZED-BY-CAP: the read itself hit degenerate_result_max_read_bytes --
# The most oversized case is also the one jq cannot see: a payload this large is
# truncated mid-JSON, `jq -e .` fails, and without this branch the hook falls
# through to silence — the exact result a size warning exists to prevent. `wc -c`
# on the read (not the original tool output, which this hook never sees whole)
# measures whether the CAP was hit, which is sufficient: at or above the cap the
# result is oversized regardless of where `tool_output_size_warning_bytes` sits.
#
# jq cannot parse this payload, so NOTHING payload-derived keys the cooldown: a
# streaming shell scan cannot tell the top-level session_id from one nested in a
# large tool_response ahead of it, and a nested one would make sessions share a
# cooldown. The key is the environment's session identity (TRW_SESSION_ID, the
# one trw_pin_key prefers everywhere) or, absent that, none: the advisory then
# fires ungated -- a per-invocation cooldown that never suppresses. Repeating an
# advisory is the lesser harm than one session silencing another's.
_payload_len=$(printf '%s' "$_payload" | wc -c | tr -d ' ') || _payload_len=0
if [ "$_payload_len" -ge "$_max_bytes" ] 2>/dev/null; then
  _dr_past_deadline && exit 0
  if _dr_prepare_dirs; then
    if [ -z "${TRW_SESSION_ID:-}" ] \
      || _dr_gate "$_state_dir/tool-output-size-$(_dr_safe_key '').state" "$_cooldown" "oversized-cap"; then
      _dr_emit "$_SIZE_LINE"
      log_hook_execution "PostToolUse:tool-output-size" "oversized-cap" "0" "advisory=emitted"
    fi
  fi
  exit 0
fi

# Malformed JSON is silence, not an error (NFR02).
printf '%s' "$_payload" | jq -e . >/dev/null 2>&1 || exit 0

# Render tool_response to text WHATEVER its shape: a string renders to itself, an
# object to its string leaves. That is deliberately generic — clients disagree
# about the shape (`{"stdout":…,"stderr":…}` for one tool, `{"file":{…}}` for
# another) and hard-coding one client's field names would make rule 1 answer
# "not empty" for every result of every other shape.
_rendered=$(printf '%s' "$_payload" | jq -r '.tool_response // "" | [.. | strings] | join("\n")' 2>/dev/null) || exit 0
_tool=$(printf '%s' "$_payload" | _json_get --strings .tool_name) || _tool=''
_command=$(printf '%s' "$_payload" | _json_get --strings .tool_input.command) || _command=''

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

# --- rule 4: oversized (PRD-INFRA-194-FR04), independent of rules 1-3 --------
# Measured on the SAME rendered text rule 1 already computed above — no second
# read, no second render.
_oversize=0
_rendered_len=$(printf '%s' "$_rendered" | wc -c | tr -d ' ') || _rendered_len=0
if [ "$_rendered_len" -gt "$_size_threshold" ] 2>/dev/null; then
  _oversize=1
fi

[ -n "$_shape" ] || [ "$_oversize" = "1" ] || exit 0
_dr_past_deadline && exit 0

# --- the cooldown, keyed by THIS session (NFR05) ------------------------------
# Two sessions in one repository keep independent counters, because the state
# path carries trw_pin_key. A session with no identity gets its own "unpinned"
# slot rather than sharing another session's.
_dr_prepare_dirs || exit 0
_safe_key=$(_dr_safe_key "$(printf '%s' "$_payload" | _json_get --strings .session_id)")

# --- fire each family through its OWN cooldown, then combine into ONE line ---
# A single PostToolUse response carries one additionalContext string, so when
# both families fire on the same call this concatenates them with a single
# space rather than emitting two JSON objects (invalid) or dropping one.
_shape_line=''
_size_line=''

if [ -n "$_shape" ]; then
  if _dr_gate "$_state_dir/degenerate-advisory-$_safe_key.state" "$_cooldown" "$_shape"; then
    _shape_line="This result does not distinguish 'absent' from 'could not look' — confirm before concluding."
  fi
fi

if [ "$_oversize" = "1" ]; then
  if _dr_gate "$_state_dir/tool-output-size-$_safe_key.state" "$_cooldown" "size"; then
    _size_line="$_SIZE_LINE"
  fi
fi

_combined="$_shape_line"
if [ -n "$_size_line" ]; then
  _combined="${_combined:+$_combined }$_size_line"
fi

if [ -n "$_combined" ]; then
  _dr_emit "$_combined"
fi
if [ -n "$_shape_line" ]; then
  log_hook_execution "PostToolUse:degenerate-result" "$_shape" "0" "advisory=emitted"
fi
if [ -n "$_size_line" ]; then
  log_hook_execution "PostToolUse:tool-output-size" "oversized" "0" "advisory=emitted"
fi
exit 0
