#!/bin/sh
# PRD-INFRA-024-FR02 + PRD-CORE-095 + PRD-FIX-124 + PRD-CORE-247: UserPromptSubmit hook.
# Three independent limbs, deliberately NOT coupled:
#
#   1. Phase guidance (PRD-CORE-095 FR01-FR06) — one calibrated line per phase
#      TRANSITION. Suppressed when the phase is unchanged, and silent at "done".
#   2. Auto-recall (PRD-CORE-095 FR07-FR14, repaired by PRD-FIX-124) — surfaces
#      a stored learning matching the prompt. Runs on EVERY prompt: it takes no
#      input from infer_phase, because a delivered run does not mean the next
#      prompt needs no context (PRD-FIX-124 FR03/FR04).
#   3. Absent-MCP-surface detection (PRD-CORE-247 FR01/FR02) — when no trw_ tool
#      invocation has been observed since the SessionStart epoch marker, past a
#      grace window and a prompt threshold, prints the offline protocol ONCE per
#      session. Fail-open toward "the surface is present": every unreadable input
#      yields silence.
#
# Fail-open: never blocks prompts. Output target: <150 tokens per phase line
# plus at most auto_recall_max_tokens of recall.
#
# Time budget (feedback #159/#160/#161): the client kills a UserPromptSubmit hook at its `timeout` (10 s) and
# discards ALL of its output, so the Python this hook starts is bounded IN-PROCESS. The hook computes one
# absolute deadline (epoch ms, TRW_HOOK_BUDGET_MS from now, default 7000) at entry and exports it as
# TRW_HOOK_DEADLINE_MS; every python it launches (the recall module, the no-jq JSON fallback in lib-trw.sh)
# arms a daemon watchdog thread before its heavy imports that exits 0 at the deadline, writing nothing
# partial. Recall gets a tighter deadline of its own (TRW_AUTO_RECALL_DEADLINE_MS, default 2000 ms from the
# start of the recall step), ONE instant shared by every interpreter candidate. A python that never reaches
# its watchdog (a hung interpreter start) is killed by a shell backstop (_trw_wait_bounded in lib-trw.sh):
# that single child pid, nothing else. The pure-shell steps are fast and stay unbounded. An overrun skips
# recall injection, logs decision=deadline, and still emits the rest of the output. macOS has no timeout(1).
set -e
trap 'exit 0' EXIT
_ups_default_budget_ms=7000
_ups_default_recall_ms=2000

# _ups_ms VALUE DEFAULT: a budget in milliseconds as a plain decimal, never octal (`08`, `0400`), at most
# 60000; anything unparsable, zero, or longer than five digits falls back to DEFAULT or the cap.
_ups_ms() {
  _um_v=$1
  case "$_um_v" in '' | *[!0-9]*)
    printf '%s' "$2"
    return 0
    ;;
  esac
  while :; do
    case "$_um_v" in 0?*) _um_v=${_um_v#0} ;; *) break ;; esac
  done
  if [ "${#_um_v}" -gt 5 ]; then
    printf '%s' 60000
  elif [ "$_um_v" -le 0 ]; then
    printf '%s' "$2"
  elif [ "$_um_v" -gt 60000 ]; then
    printf '%s' 60000
  else
    printf '%s' "$_um_v"
  fi
}

_hook_dir="$(cd "$(dirname "$0")" && pwd)" || exit 0
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

# The deadline, the private directory (every capture file lives in it, the raw prompt among them) and the
# cleanup: one idempotent function on EXIT and on INT/TERM/HUP, so a signal during cleanup cannot cut it short.
_TRW_UPS_WORK=""
_TRW_BG_PID=""
_ups_done=""
_ups_cleanup() {
  [ -z "$_ups_done" ] || return 0
  _ups_done=1
  trap '' INT TERM HUP
  [ -z "${_TRW_BG_PID:-}" ] || kill -KILL "$_TRW_BG_PID" 2>/dev/null || true
  [ -z "${_TRW_UPS_WORK:-}" ] || rm -rf "$_TRW_UPS_WORK" 2>/dev/null || true
}
# Installed BEFORE the directory exists (its name is still empty, which cleanup skips), so a signal in the gap
# between mktemp creating it and the variable being set cannot leak it.
trap '_ups_cleanup; exit 0' EXIT
trap 'exit 0' INT TERM HUP
# A client that closed its end of stdout must cost a failed write, not this process: cleanup still runs.
trap '' PIPE
if command -v _trw_now_ms >/dev/null 2>&1; then
  _trw_now_ms
  TRW_HOOK_DEADLINE_MS=$((_trw_now + $(_ups_ms "${TRW_HOOK_BUDGET_MS:-}" "$_ups_default_budget_ms")))
  export TRW_HOOK_DEADLINE_MS
  _TRW_UPS_WORK=$(mktemp -d "${TMPDIR:-/tmp}/trw-ups.XXXXXX" 2>/dev/null) || _TRW_UPS_WORK=""
  export _TRW_UPS_WORK
fi

# FR07: Read stdin JSON and extract prompt text (replaces cat >/dev/null)
_payload=$(cat) || exit 0
_prompt=$(printf '%s' "$_payload" | _json_get .prompt) || _prompt=""

# PRD-FIX-124 FR11: hand infer_phase THIS session's identity so it resolves the
# run we own instead of whichever run sorted newest project-wide. jq or python3
# (T29); empty is the honest "identity unknown" state, for which infer_phase
# prints "none" (R2-009), and a host with neither logs one diagnostic saying so.
_stdin_session_id=$(_json_str_field "$_payload" session_id) || _stdin_session_id=""
_trw_has_json_parser || log_hook_execution "UserPromptSubmit" "unknown" "0" "jq_unavailable=1"

_phase=$(infer_phase "$_stdin_session_id")
_project_root="$(get_repo_root)" || exit 0
_context_dir="$_project_root/.trw/context"
[ -d "$_context_dir" ] || mkdir -p "$_context_dir" 2>/dev/null || true

# FR07: malformed or missing prompt input must stay fully silent.
if [ -z "$_prompt" ]; then
  log_hook_execution "UserPromptSubmit" "$_phase" "skipped"
  exit 0
fi

# --- PRD-CORE-247-FR01/FR02: absent-MCP-surface detection (third limb) ---
#
# A THIRD limb, decoupled from the other two exactly as they are from each other.
# It runs before the phase-guidance limb because when it fires, its block is the
# only guidance worth reading: everything below names trw_ tools this session
# does not have.
#
# The detector, the tunables, and the emitted text all live in lib-trw.sh. The
# `command -v` guard is not defensive clutter — a project whose .claude/hooks/
# copy of lib-trw.sh predates PRD-CORE-247 must keep working, and the fail-open
# direction for this whole subsystem is "the surface is present" (silence).
#
# PRD-FIX-128-FR06: every one of the four calls takes THIS session's identity.
# `_stdin_session_id` is the same value the other two limbs already use, and
# `trw_pin_key` prefers the exported session variable over it, so precedence is
# unchanged. Threading it is what makes the keyed epoch, the keyed latch, and
# the owned-run scan reachable on a client whose profile publishes no session
# variable -- which is every profile except claude-code, and therefore every
# tree whose generated hook-env.d/<key>.sh was written by one of them. An older library
# that predates FR128 ignores the extra argument, so the guard above still holds.
_degraded_emitted=0
if command -v trw_bump_session_prompt_index >/dev/null 2>&1; then
  _prompt_index=$(trw_bump_session_prompt_index "$_stdin_session_id")
  if ! trw_degraded_latched "$_stdin_session_id" \
    && trw_degraded_mode_detected "$_prompt_index" "$_stdin_session_id"; then
    trw_emit_offline_protocol_block \
      "$(trw_session_epoch_ts "$_stdin_session_id" 2>/dev/null || printf '')" "$_stdin_session_id"
    echo ""
    _degraded_emitted=1
  fi
fi

# --- PRD-CORE-095 FR01-FR06: Phase-change suppression (phase-guidance limb) ---
_phase_cache="$_context_dir/last_ups_phase"
_injected_file="$_context_dir/injected_learning_ids.txt"

# FR04: "none" phase has no phase-CHANGE to suppress on (it is one value, not
# a transition), so it falls through to a cadence cap instead (PRD-CORE-301
# cut 1): the phase-change suppressor below never fires for it, and without a
# cap a session that never calls trw_session_start re-prints this line on
# every single prompt for the rest of the session.
_NONE_PHASE_CADENCE=5
if [ "$_phase" != "none" ]; then
  _cached_phase=$(_trw_safe_read "$_phase_cache") || _cached_phase=""
  # FR02: Same-phase suppression — skip PHASE OUTPUT if unchanged. PRD-FIX-124
  # FR04 narrows this to the guidance limb; auto-recall below never reads it.
  if [ "$_cached_phase" = "$_phase" ]; then
    _phase_suppressed=1
  else
    _phase_suppressed=0
  fi
  # FR01: Write current phase to cache (atomic write, never through a symlink)
  printf '%s' "$_phase" | _trw_safe_write "$_phase_cache" || true
  # A later prompt can return to "none" (e.g. a delivered run's pin retired),
  # so the cadence counter below must not still read as "mid-run" the next
  # time the phase goes back to "none". Clearing it here keeps the cadence
  # cap counting THIS "none" streak, not a stale one from earlier in the
  # session.
  _trw_safe_rm "$_context_dir/none_phase_prompt_count" || true
else
  # PRD-CORE-301 cut 1: emit on the first "none" prompt, then at most once
  # every _NONE_PHASE_CADENCE prompts after that — never a hard silence,
  # because the agent still needs the reminder eventually, and never every
  # prompt, because that is the leak this cut removes.
  _none_counter_file="$_context_dir/none_phase_prompt_count"
  _none_count=$(_trw_safe_read "$_none_counter_file") || _none_count=0
  case "$_none_count" in ''|*[!0-9]*) _none_count=0 ;; esac
  _none_count=$((_none_count + 1))
  printf '%s' "$_none_count" | _trw_safe_write "$_none_counter_file" || true
  if [ $(( (_none_count - 1) % _NONE_PHASE_CADENCE )) -eq 0 ]; then
    _phase_suppressed=0
  else
    _phase_suppressed=1
  fi
fi

# Emit phase guidance if not suppressed
_emitted_any=$_degraded_emitted
if [ "$_phase_suppressed" = "0" ]; then
  case "$_phase" in
    none)
      echo "TRW: Call trw_session_start(query='your task domain') to load context, then read the EXECUTION MODEL SUMMARY and PHASES sections of .trw/frameworks/FRAMEWORK.md — they define the methodology your tools implement."
      _emitted_any=1
      ;;
    early)
      echo "TRW [RESEARCH/PLAN]: PRD validation gates implementation — trw_prd_validate catches ambiguity before it becomes rework."
      _emitted_any=1
      ;;
    plan)
      echo "TRW [PLAN]: Run trw_prd_validate before implementing — a spec gap found now is an edit; found during implementation it is a rewrite."
      _emitted_any=1
      ;;
    implement)
      echo "TRW [IMPLEMENT]: Before completing, re-read FRs for coverage gaps. Call trw_checkpoint after milestones — uncheckpointed work is lost on compaction."
      _emitted_any=1
      ;;
    validate)
      echo "TRW [VALIDATE]: Run applicable project-native checks, then record observed counts/status with trw_build_check(tests_passed, test_count, failure_count, static_checks_clean, scope). The reporter does not run checks."
      _emitted_any=1
      ;;
    deliver)
      echo "TRW [DELIVER]: trw_deliver() persists learnings, syncs the client instruction file, and closes the run — without it, your session's work is invisible to future agents."
      _emitted_any=1
      ;;
    done)
      # Silent — the RUN is complete, so there is no next phase to guide toward
      # (PRD-CORE-095 FR06). This silence is scoped to the guidance limb only;
      # PRD-FIX-124 FR03 keeps auto-recall running below.
      ;;
  esac
fi

# --- PRD-CORE-095 FR07-FR14 / PRD-FIX-124: Contextual learning auto-injection ---

# FR14: Config gate — check auto_recall_enabled. Every value below mirrors a
# typed TRWConfig field; the inline literals are the no-config-file fallbacks
# and are asserted equal to the Pydantic defaults by
# tests/test_auto_recall_scoring.py::test_default_threshold_is_recalibrated.
_auto_recall_enabled="true"
_auto_recall_max_results=3
_auto_recall_max_tokens=100
_auto_recall_min_score="0.35"
_auto_recall_scan_cap=10000
_config_file="$_project_root/.trw/config.yaml"
if [ -f "$_config_file" ]; then
  # One read of the file, no forks (feedback #160). A line is `key: value`: leading blanks are dropped, a line
  # that starts with `#` is a comment, the key is the text before the first colon and must equal a tunable
  # exactly, the value is the text after it up to a ` #` comment, with blanks and quote characters dropped.
  # (No `${..${..}}` nesting below: doctor's static call scanner reads a `${..}` up to its first `}`.)
  # The first line holding each key wins.
  _cfg_seen=""
  _cfg_tab=$(printf '\t')
  while IFS= read -r _cfg_line || [ -n "$_cfg_line" ]; do
    _cfg_ws=${_cfg_line%%[! $_cfg_tab]*}
    _cfg_line=${_cfg_line#"$_cfg_ws"}
    case "$_cfg_line" in '#'* | '' | *:*) ;; *) continue ;; esac
    case "$_cfg_line" in '#'* | '') continue ;; esac
    _cfg_key=${_cfg_line%%:*}
    case "$_cfg_key" in
      auto_recall_enabled | auto_recall_max_results | auto_recall_max_tokens | auto_recall_min_score | auto_recall_scan_cap) ;;
      *) continue ;;
    esac
    case " $_cfg_seen " in *" $_cfg_key "*) continue ;; esac
    _cfg_seen="$_cfg_seen $_cfg_key"
    _val=${_cfg_line#*:}
    _cfg_ws=${_val%%[! $_cfg_tab]*}
    _val=${_val#"$_cfg_ws"}
    case "$_val" in '#'*) _val="" ;; esac
    _val=${_val%%[ $_cfg_tab]#*}
    while :; do
      case "$_val" in
        *[\"\']*)
          _val_pre=${_val%%[\"\']*}
          _val_rest=${_val#"$_val_pre"?}
          _val=$_val_pre$_val_rest
          ;;
        *) break ;;
      esac
    done
    _cfg_ws=${_val##*[! $_cfg_tab]}
    _val=${_val%"$_cfg_ws"}
    [ -n "$_val" ] || continue
    case "$_cfg_key" in
      auto_recall_enabled) _auto_recall_enabled="$_val" ;;
      auto_recall_max_results) _auto_recall_max_results="$_val" ;;
      auto_recall_max_tokens) _auto_recall_max_tokens="$_val" ;;
      auto_recall_min_score) _auto_recall_min_score="$_val" ;;
      auto_recall_scan_cap) _auto_recall_scan_cap="$_val" ;;
    esac
  done <"$_config_file"
fi
# Env var overrides
[ -n "$TRW_AUTO_RECALL_ENABLED" ] && _auto_recall_enabled="$TRW_AUTO_RECALL_ENABLED"
[ -n "$TRW_AUTO_RECALL_MAX_RESULTS" ] && _auto_recall_max_results="$TRW_AUTO_RECALL_MAX_RESULTS"
[ -n "$TRW_AUTO_RECALL_MAX_TOKENS" ] && _auto_recall_max_tokens="$TRW_AUTO_RECALL_MAX_TOKENS"
[ -n "$TRW_AUTO_RECALL_MIN_SCORE" ] && _auto_recall_min_score="$TRW_AUTO_RECALL_MIN_SCORE"
[ -n "$TRW_AUTO_RECALL_SCAN_CAP" ] && _auto_recall_scan_cap="$TRW_AUTO_RECALL_SCAN_CAP"
_auto_recall_deadline_ms=$(_ups_ms "${TRW_AUTO_RECALL_DEADLINE_MS:-}" "$_ups_default_recall_ms")
# The learning_recall_enabled master switch outranks auto_recall_enabled and its env
# override. The guard keeps a project whose lib-trw.sh predates the helper working.
if command -v trw_learnings_injection_allowed >/dev/null 2>&1 && ! trw_learnings_injection_allowed; then
  _auto_recall_enabled="false"
fi

# Early exit if disabled
if [ "$_auto_recall_enabled" = "false" ]; then
  log_hook_execution "UserPromptSubmit" "$_phase" "skipped"
  exit 0
fi

# PRD-CORE-333 FR03: the candidates come from a FILTERED store read, never from the
# .trw/learnings entries mirror. `python -m trw_mcp.state._auto_recall_hook` asks the
# checkout's store (the daemon), whose backend reads drop every identity the quarantine
# ledger blocks (StorageBackend.filter_quarantined), then runs the PRD-FIX-124 scorer.
# No interpreter with trw_mcp installed, or no reachable store, means no recall:
# the hook fails closed rather than reading the unfiltered mirror.
#
# FR05: the scorer's diagnostic goes to stderr (stdout is injected into the
# model's context and must carry recall text only). Capture it so it can also be
# forwarded to the durable hook log, then replay it for interactive debugging. The
# capture lives in the hook's private temp directory, never in the checkout
# (PRD-FIX-156-FR03: nothing here can be redirected through a planted symlink).
# Interpreter order, as the intent guard resolves it: $TRW_PYTHON, the project venv, the
# interpreter behind the `trw-mcp` launcher on PATH, then PATH python3.
_recall_launcher_py=""
_recall_launcher=$(command -v trw-mcp 2>/dev/null) &&
  _recall_launcher_py=$(head -n 1 "$_recall_launcher" 2>/dev/null | sed -n 's/^#!\([^ ]*\).*/\1/p')
_recall_output=""
_recall_ran=""
_recall_deadline_hit=""
_recall_budget_ms=""
_recall_skip=""
_recall_record=""
_recall_pending=""
_diag_file=""
if [ -z "${_TRW_UPS_WORK:-}" ]; then
  _recall_skip=no_workdir
else
  _recall_in="$_TRW_UPS_WORK/recall.in"
  _recall_out="$_TRW_UPS_WORK/recall.out"
  _recall_pending="$_TRW_UPS_WORK/recall.ids"
  _diag_file="$_TRW_UPS_WORK/recall.diag"
  # The prompt travels on stdin ("-"): as an argument, one past ARG_MAX (128 KB on Linux) fails the exec
  # and recall silently never runs. printf is a builtin, so the shell itself has no such limit.
  printf '%s' "$_prompt" >"$_recall_in" 2>/dev/null || : >"$_recall_in"
  # ONE recall deadline, an absolute instant fixed before the first candidate and shared by all of them: a
  # candidate that cannot start never resets the clock. It sits a second inside the hook deadline, which
  # is reserved for emitting and logging. The module's own 500 ms scan deadline starts only after
  # interpreter start, imports and the store read, so it cannot bound those.
  _trw_now_ms
  _recall_deadline=$((_trw_now + _auto_recall_deadline_ms))
  _recall_cap=$((TRW_HOOK_DEADLINE_MS - 1000))
  [ "$_recall_deadline" -le "$_recall_cap" ] || _recall_deadline=$_recall_cap
  _recall_budget_ms=$((_recall_deadline - _trw_now))
  # The module runs as `python -c <boot> <module> ...`: the boot arms the watchdog, then runs <module>.
  _recall_boot="$_TRW_PY_WATCHDOG
import runpy, sys
runpy.run_module(sys.argv.pop(1), run_name=\"__main__\", alter_sys=True)
"
  for _recall_py in "${TRW_PYTHON:-}" "$_project_root/.venv/bin/python" "$_project_root/.venv/bin/python3" \
    "$_recall_launcher_py" "$(command -v python3 2>/dev/null)"; do
    [ -n "$_recall_py" ] && [ -x "$_recall_py" ] || continue
    _trw_now_ms
    if [ "$_trw_now" -ge "$_recall_deadline" ]; then
      _recall_deadline_hit=1
      _recall_ran=1
      break
    fi
    # Every module, new or released, reads its history from and appends its new ids to the dedup argument.
    # That argument is a scratch COPY of the real file, so each sees the true history and none ever writes
    # (or is even told) the real path; it is moved over only once the text has reached the client.
    _trw_safe_read "$_injected_file" >"$_recall_pending" 2>/dev/null || : >"$_recall_pending"
    TRW_HOOK_DEADLINE_MS=$_recall_deadline "$_recall_py" -c "$_recall_boot" trw_mcp.state._auto_recall_hook \
      "$_project_root" - "$_recall_pending" \
      "$_auto_recall_max_results" "$_auto_recall_max_tokens" "$_auto_recall_min_score" "$_auto_recall_scan_cap" \
      <"$_recall_in" >"$_recall_out" 2>"$_diag_file" &
    _TRW_BG_PID=$!
    _recall_rc=0
    _trw_wait_bounded "$_recall_deadline" || _recall_rc=$?
    if [ "$_recall_rc" = "124" ]; then
      _recall_deadline_hit=1
      _recall_ran=1
      break
    fi
    # 1 = the module could not start under this interpreter (trw_mcp not installed): try the next.
    [ "$_recall_rc" != "1" ] || continue
    _recall_ran=1
    _recall_record=$(grep -m1 '^event=AutoRecall .* elapsed_ms=[0-9][0-9]*$' "$_diag_file" 2>/dev/null) || _recall_record=""
    if [ -z "$_recall_record" ] && [ "$_recall_rc" = "0" ]; then
      # A clean exit with no complete record is the in-process watchdog: the deadline cut the module off.
      _recall_deadline_hit=1
    else
      _recall_output=$(cat "$_recall_out" 2>/dev/null) || _recall_output=""
    fi
    break
  done
fi

# This shell is the only writer of the log record: the module's record is read from its capture, or replaced
# when a deadline cut it off (a half-written record is no record).
_diag=""
if [ -n "$_recall_deadline_hit" ]; then
  _diag="event=AutoRecall keywords=0 scanned=0 top_score=0.000 top_id=none threshold=$_auto_recall_min_score injected=0 decision=deadline elapsed_ms=$_recall_budget_ms"
else
  _diag=$_recall_record
  if [ -n "$_diag_file" ] && [ -f "$_diag_file" ]; then
    cat "$_diag_file" >&2 2>/dev/null || true
  fi
  # FR05 still holds with no interpreter: exactly one record per prompt.
  [ -n "$_recall_ran" ] || _diag="event=AutoRecall keywords=0 scanned=0 top_score=0.000 top_id=none threshold=$_auto_recall_min_score injected=0 decision=${_recall_skip:-no_interpreter} elapsed_ms=0"
fi

# The text goes out first; the scratch history (the old ids plus the new) replaces the real dedup file only if
# the client's pipe took it, so a hook cancelled or cut off before this point never marks a learning delivered.
if [ -n "$_recall_output" ]; then
  if printf '%s\n' "$_recall_output"; then
    _emitted_any=1
    if [ -s "$_recall_pending" ]; then
      _trw_safe_write "$_injected_file" <"$_recall_pending" || true
    fi
  fi
fi

if [ "$_emitted_any" = "1" ]; then
  _phase_status="emitted"
elif [ "$_phase_suppressed" = "1" ]; then
  _phase_status="cached"
else
  _phase_status="silent"
fi

log_hook_execution "UserPromptSubmit" "$_phase" "$_phase_status" "$_diag"
exit 0
