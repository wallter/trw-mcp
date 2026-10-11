#!/bin/sh
# PRD-INFRA-002-FR05: PreCompact hook — state snapshot.
# Saves this session's run state to its pre-compaction marker
# (pre_compact_state_file in lib-trw.sh)
# before context compaction so it can be recovered afterwards.
# Fail-open: any error silently exits 0.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

_project_root="$(get_repo_root)" || exit 0
_context_dir="$_project_root/.trw/context"
[ -d "$_context_dir" ] || mkdir -p "$_context_dir" 2>/dev/null || exit 0
_injected_file="$_context_dir/injected_learning_ids.txt"

# PRD-CORE-095 FR17: clear injected-learning dedup state before compaction.
printf '' | _trw_safe_write "$_injected_file" || true

# Determine trigger type from stdin
_payload=$(cat) || exit 0
# PreCompact names it "trigger" (manual|auto); "source" is kept for clients that
# send that key. jq or python3 (PRD-FIX-149 FR06): without either both reads
# below return empty and _trigger falls through to "unknown" -- never a guessed value.
_trigger=$(_json_str_field "$_payload" trigger) || _trigger=""
[ -n "$_trigger" ] || _trigger=$(_json_str_field "$_payload" source) || _trigger=""
[ -n "$_trigger" ] || _trigger="unknown"

_ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)" || _ts="unknown"

# --- Find THIS SESSION'S OWN run (PRD-FIX-118 FR03) ------------------------
# This snapshot is what session-start.sh replays back into the
# model as "RECOVERED RUN / RECOVERED PHASE" after compaction. Snapshotting the
# newest run therefore does not just mislabel a field: it tells a freshly
# compacted agent to resume inside a parallel instance's run, at that run's phase,
# quoting that run's checkpoint text.
#
# What "unowned" means HERE: still write the snapshot, with the run-scoped fields
# EMPTY. The hook has real non-run-scoped duties (clearing the injected-learning
# dedup file, recording the compaction trigger, arming the recovery marker that
# middleware/ceremony.py keys on existence), and both readers already degrade
# correctly on an empty run_path ("No active run found in pre-compaction
# snapshot"). Skipping the write would disarm recovery entirely.
_session_id=$(_json_str_field "$_payload" session_id) || _session_id=""
_session_id=$(trw_pin_key "$_session_id" 2>/dev/null) || _session_id=""

_run_dir=""
_ownership="unowned"
if [ -n "$_session_id" ]; then
  _run_dir=$(resolve_owned_run "$_session_id" 2>/dev/null) || _run_dir=""
  [ -n "$_run_dir" ] && _ownership="owned"
fi
# PRD-FIX-149 review R4: when identity is unresolvable (no TRW_SESSION_ID and
# either no JSON parser or no session_id in the payload), this is NOT distinguishable
# from "N instances live, none identified" -- the exact shape PRD-FIX-118
# exists to refuse. The removed ``else`` branch used to call find_active_run()
# here ("legacy newest-wins, correct for a single-instance install"), which
# reintroduced that cross-instance resume bug: a genuinely unowned session got
# handed WHICHEVER run was newest, and this snapshot is what session-start.sh
# replays back as "RECOVERED RUN". _run_dir/_ownership
# above already default to empty/"unowned" for exactly this case.

# --- What the checkpoint tool recorded for THIS session survives the hook (PRE-COMPACT-KEEP) ---
# trw_checkpoint(pre_compact=True, ...) writes this same per-session marker earlier in the session: the directive,
# the context anchor and, for a session with no pinned run, the run the caller named. This hook then replaced the
# whole document from the pin alone, so by the time the compacted session read it all three were gone, and an
# unpinned session recovered no run. They are read back before the rewrite, under four conditions:
#   * the file is the per-session one, a regular file and not a link. The project-wide fallback may be another
#     session's and carries nothing over (PRD-FIX-118);
#   * one identity: the key this hook resolves pins with names that same file. A payload that names another
#     session than the environment does carries nothing over;
#   * the document is the TOOL's, not an earlier snapshot of this hook: the tool writes "timestamp" and no
#     "ownership"; this hook writes "ownership". A run this hook once derived from a pin is never revived from
#     its own old snapshot after the pin is gone;
#   * a named run is used only when no pin resolved one, and only if it is a directory inside this project by
#     its real location, with meta/run.yaml, named without ".." or control characters.
# What this does not establish: who wrote the file. Its name is the only evidence of ownership, here as in
# session-start.sh, which replays whatever this session's marker holds.
_state_file=$(pre_compact_state_file "$_project_root" 2>/dev/null) || exit 0
_kept_directive=""
_kept_anchor=""
_own_marker=""
[ -z "$_session_id" ] || _own_marker=$(TRW_SESSION_ID="$_session_id"; pre_compact_state_file "$_project_root" 2>/dev/null) || _own_marker=""
case "$_state_file" in
  */.trw/context/pre_compact/*.json)
    if [ "$_state_file" = "$_own_marker" ] && [ -f "$_state_file" ] && [ ! -L "$_state_file" ]; then
      _kept_stamp=$(_json_get --file "$_state_file" .timestamp 2>/dev/null) || _kept_stamp=""
      _kept_owner=$(_json_get --file "$_state_file" .ownership 2>/dev/null) || _kept_owner="unreadable"
      if [ -n "$_kept_stamp" ] && [ -z "$_kept_owner" ]; then
        _kept_directive=$(_json_get --file "$_state_file" .directive 2>/dev/null) || _kept_directive=""
        _kept_anchor=$(_json_get --file "$_state_file" .context_anchor 2>/dev/null) || _kept_anchor=""
        if [ -z "$_run_dir" ]; then
          # Read with a sentinel: a command substitution drops trailing newlines, and a name that ends in one
          # would otherwise pass the control-character test as the directory without it.
          _kept_run=$(_json_get --file "$_state_file" .run_path 2>/dev/null && printf x) || _kept_run=""
          _kept_run="${_kept_run%x}"
          _kept_run="${_kept_run%
}"
          _kept_run="${_kept_run%/}"
          case "$_kept_run" in
            */../* | */.. | *[[:cntrl:]]*) _kept_run="" ;;
          esac
          case "$_kept_run" in
            "$_project_root"/?*)
              _kept_real=$(cd "$_kept_run" 2>/dev/null && pwd -P) || _kept_real=""
              _root_real=$(cd "$_project_root" 2>/dev/null && pwd -P) || _root_real=""
              case "$_kept_real" in
                "$_root_real"/?*)
                  # The real location, spelled from the project root this hook was given.
                  _kept_run="$_project_root/${_kept_real#"$_root_real"/}"
                  if [ -n "$_root_real" ] && [ -f "$_kept_run/meta/run.yaml" ]; then
                    _run_dir="$_kept_run/"
                    _ownership="marker"
                  fi
                  ;;
              esac
              ;;
          esac
        fi
      fi
    fi
    ;;
esac

_run_path=""
_phase=""
_event_count=0
_last_checkpoint=""
_active_tasks=0
_pending_decisions=""
_cp_line=""

if [ -n "$_run_dir" ]; then
  _run_path="$_run_dir"

  # Extract phase from run.yaml
  _run_yaml="${_run_dir}meta/run.yaml"
  if [ -f "$_run_yaml" ]; then
    _phase=$(grep '^phase:' "$_run_yaml" | head -1 | sed 's/^phase:[[:space:]]*//' | tr -d "'" | tr -d '"') || true
  fi

  # Count events
  _events_path="${_run_dir}meta/events.jsonl"
  if [ -f "$_events_path" ]; then
    _event_count=$(wc -l < "$_events_path" 2>/dev/null | tr -d ' ') || _event_count=0
  fi

  # Get last checkpoint message: the newest of the last 50 lines that does not carry the checkpoint tool's fixed
  # pre-compaction text. That line is usually the newest one here, so the newest line alone named the fixed text,
  # not what the session last recorded (PRD-CORE-342 FR-02). With nothing else in reach, the newest line stands.
  _cp_path="${_run_dir}meta/checkpoints.jsonl"
  if [ -f "$_cp_path" ]; then
    _cp_line=$(tail -n 50 "$_cp_path" 2>/dev/null | grep -v '"message":[[:space:]]*"pre-compaction safety checkpoint"' | tail -1) || _cp_line=""
    [ -n "$_cp_line" ] || _cp_line=$(tail -1 "$_cp_path" 2>/dev/null) || _cp_line=""
    _last_checkpoint=$(printf '%s\n' "$_cp_line" | _json_get .message) || true
  fi

  # FR02: active_tasks — count in-progress tasks from task directory
  _task_dir="${_run_dir}tasks"
  if [ -d "$_task_dir" ]; then
    _active_tasks=$(grep -rl '"status"[[:space:]]*:[[:space:]]*"in_progress"\|^status:[[:space:]]*in_progress' "$_task_dir" 2>/dev/null | wc -l | tr -d ' ') || _active_tasks=0
  fi

  # FR02: pending_decisions — open questions from that same checkpoint line
  if [ -f "$_cp_path" ]; then
    _pending_decisions=$(printf '%s\n' "$_cp_line" | _json_get .pending_decisions .open_questions) || true
  fi
fi

# Write state snapshot to _state_file, resolved above: this session's own marker, or the project-wide file when
# the session has no identity in its environment (that file records the key it was written for).
# PRD-FIX-154 FR02: _json_object builds this snapshot on both the jq and the
# jq-less path, so an operator without jq no longer loses last_checkpoint,
# active_tasks and pending_decisions, and run_path/phase are
# always escaped (they are unowned-session data, not trusted literals).
_json_object \
  --str ts "$_ts" \
  --str trigger "$_trigger" \
  --str run_path "$_run_path" \
  --str phase "$_phase" \
  --int events_logged "${_event_count:-0}" \
  --str last_checkpoint "$_last_checkpoint" \
  --int active_tasks "${_active_tasks:-0}" \
  --str pending_decisions "$_pending_decisions" \
  --str ownership "$_ownership" \
  --str session_key "$_session_id" \
  --str directive "$_kept_directive" \
  --str context_anchor "$_kept_anchor" \
  | _trw_safe_write "$_state_file" 2>/dev/null

# PRD-FIX-149 FR06: one explicit diagnostic when neither jq nor python3 could read the
# trigger/session_id fields above -- never a silently degraded event.
_le_detail=""
_trw_has_json_parser || _le_detail="jq_unavailable=1"
log_hook_execution "PreCompact" "$_trigger" "0" "$_le_detail"

exit 0
