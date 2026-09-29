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
# Performance: ~71ms baseline (infer_phase); a full 6.4k-entry scan + IDF scan
# measures ~190ms warm against the 500ms deadline (PRD-FIX-124 NFR01).
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

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
  _val=$(grep -m1 'auto_recall_enabled:' "$_config_file" 2>/dev/null | sed 's/.*: *//' | tr -d "\"'" 2>/dev/null) || true
  [ -n "$_val" ] && _auto_recall_enabled="$_val"
  _val=$(grep -m1 'auto_recall_max_results:' "$_config_file" 2>/dev/null | sed 's/.*: *//' | tr -d "\"'" 2>/dev/null) || true
  [ -n "$_val" ] && _auto_recall_max_results="$_val"
  _val=$(grep -m1 'auto_recall_max_tokens:' "$_config_file" 2>/dev/null | sed 's/.*: *//' | tr -d "\"'" 2>/dev/null) || true
  [ -n "$_val" ] && _auto_recall_max_tokens="$_val"
  _val=$(grep -m1 'auto_recall_min_score:' "$_config_file" 2>/dev/null | sed 's/.*: *//' | tr -d "\"'" 2>/dev/null) || true
  [ -n "$_val" ] && _auto_recall_min_score="$_val"
  _val=$(grep -m1 'auto_recall_scan_cap:' "$_config_file" 2>/dev/null | sed 's/.*: *//' | tr -d "\"'" 2>/dev/null) || true
  [ -n "$_val" ] && _auto_recall_scan_cap="$_val"
fi
# Env var overrides
[ -n "$TRW_AUTO_RECALL_ENABLED" ] && _auto_recall_enabled="$TRW_AUTO_RECALL_ENABLED"
[ -n "$TRW_AUTO_RECALL_MAX_RESULTS" ] && _auto_recall_max_results="$TRW_AUTO_RECALL_MAX_RESULTS"
[ -n "$TRW_AUTO_RECALL_MAX_TOKENS" ] && _auto_recall_max_tokens="$TRW_AUTO_RECALL_MAX_TOKENS"
[ -n "$TRW_AUTO_RECALL_MIN_SCORE" ] && _auto_recall_min_score="$TRW_AUTO_RECALL_MIN_SCORE"
[ -n "$TRW_AUTO_RECALL_SCAN_CAP" ] && _auto_recall_scan_cap="$TRW_AUTO_RECALL_SCAN_CAP"
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
# forwarded to the durable hook log, then replay it for interactive debugging.
# A symlinked context dir gets no capture file: the redirect below would create
# it wherever the link points (PRD-FIX-156-FR03).
_diag_file="$_context_dir/.auto_recall_diag.$$"
_trw_ancestor_symlinked "$_context_dir" && _diag_file=/dev/null
# Interpreter order, as the intent guard resolves it: $TRW_PYTHON, the project venv, the
# interpreter behind the `trw-mcp` launcher on PATH, then PATH python3.
_recall_launcher_py=""
_recall_launcher=$(command -v trw-mcp 2>/dev/null) &&
  _recall_launcher_py=$(head -n 1 "$_recall_launcher" 2>/dev/null | sed -n 's/^#!\([^ ]*\).*/\1/p')
_recall_output=""
_recall_ran=""
for _recall_py in "${TRW_PYTHON:-}" "$_project_root/.venv/bin/python" "$_project_root/.venv/bin/python3" \
  "$_recall_launcher_py" "$(command -v python3 2>/dev/null)"; do
  [ -n "$_recall_py" ] && [ -x "$_recall_py" ] || continue
  _recall_rc=0
  _recall_output=$(
    "$_recall_py" -m trw_mcp.state._auto_recall_hook "$_project_root" "$_prompt" "$_injected_file" \
      "$_auto_recall_max_results" "$_auto_recall_max_tokens" "$_auto_recall_min_score" "$_auto_recall_scan_cap" \
      2>"$_diag_file"
  ) || _recall_rc=$?
  # 1 = the module could not start under this interpreter (trw_mcp not installed): try the next.
  [ "$_recall_rc" = "1" ] || { _recall_ran=1; break; }
  _recall_output=""
done

_diag=""
if [ -f "$_diag_file" ]; then
  _diag=$(grep -m1 '^event=AutoRecall' "$_diag_file" 2>/dev/null) || _diag=""
  cat "$_diag_file" >&2 2>/dev/null || true
  _trw_safe_rm "$_diag_file" || true
fi
# FR05 still holds with no interpreter: exactly one record per prompt.
[ -n "$_recall_ran" ] || _diag="event=AutoRecall keywords=0 scanned=0 top_score=0.000 top_id=none threshold=$_auto_recall_min_score injected=0 decision=no_interpreter elapsed_ms=0"

if [ -n "$_recall_output" ]; then
  printf '%s\n' "$_recall_output"
  _emitted_any=1
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
