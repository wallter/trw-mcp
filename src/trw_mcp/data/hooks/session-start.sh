#!/bin/sh
# PRD-INFRA-002-FR01/FR02/FR03/FR04: Unified SessionStart hook.
# Dispatches on $SOURCE (startup|resume|compact|clear) from stdin JSON.
# Framing: value-oriented — explains what each tool gives the agent.
# Research: Anthropic context engineering, motivation framing, self-interest framing.
# Fail-open: any error silently exits 0.
#
# Performance: ~23ms avg latency (benchmarked 2026-03-29, 3 runs).
# Fires once per session event, not on every tool call.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

# PRD-CORE-149 FR05: the generated profile policy gates the entire hook before
# timers, stdin reads, or output. lib-trw.sh normalizes the legacy name.
if [ "${HOOKS_ENABLED:-true}" = "false" ]; then
  exit 0
fi

init_hook_timer

# Read stdin payload to determine source
_payload=$(cat) || exit 0
_source=""
_payload_session_id=""
if command -v jq >/dev/null 2>&1; then
  _source=$(printf '%s' "$_payload" | jq -r '.source // empty' 2>/dev/null) || true
  _payload_session_id=$(printf '%s' "$_payload" | jq -r '.session_id // empty' 2>/dev/null) || true
fi
# Fallback: extract source via grep
if [ -z "$_source" ]; then
  _source=$(printf '%s' "$_payload" | grep -o '"source"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"source"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
fi
if [ -z "$_payload_session_id" ]; then
  _payload_session_id=$(printf '%s' "$_payload" | grep -o '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"session_id"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
fi

_project_root="$(get_repo_root)" || exit 0

# --- PRD-FIX-118 FR03/FR04/FR05: own-run state, never a foreign run's ---
#
# What changed and why. This used to glob every */meta/run.yaml under the task
# root, sort lexicographically, take the last, and print that run's declared
# complexity field as THIS session's ceremony tier. With several instances live
# (four concurrent servers observed 2026-07-24) the newest run is routinely
# another session's, so the tier printed here contradicted the tier
# trw_session_start resolved from the profile in the very same boot -- once
# observed as hook "MINIMAL" vs resolved COMPREHENSIVE. An agent had to guess.
#
# Two rules now hold:
#   FR05 -- one tier authority. Ceremony tier comes from
#           profile/session_resolve.py via trw_session_start. This hook does not
#           compute, read, or print a tier. Deleting the duplicate resolver is
#           the fix; reconciling two of them would not be.
#   FR04 -- an unpinned session says so. No tier, no phase, no event count, no
#           progress figure may be sourced from a run this session does not own.
_emit_run_state() {
  _own_run=$(resolve_owned_run "$_payload_session_id") || _own_run=""

  if [ -z "$_own_run" ]; then
    echo "CEREMONY: No run is pinned to this session — call trw_init(task_name) to start one."
    echo "  This session's run state is unknown, so no phase, tier, or event count is shown."
    echo "  Ceremony tier comes from trw_session_start(), the single authority."
    return
  fi

  # Sanitize before echoing into the AI context: the path originates in
  # pins.json, which is machine-written but still untrusted input to this hook.
  _own_run_rel=$(_sanitize_context_text "${_own_run#"$_project_root"/}")
  echo "CEREMONY: Run pinned to this session: $_own_run_rel"
  echo "  Call trw_session_start() for your resolved ceremony tier, then trw_status() for phase."
}

# --- Untrusted-text sanitizer for AI-context injection defense ---
# The checkpoint/last-checkpoint text in pre_compact_state.json is attacker-
# influenceable (any process that can write the file, or a crafted run). When
# echoed verbatim into the SessionStart context it can carry prompt-injection
# payloads or terminal control sequences. _sanitize_context_text:
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

# --- PRD-CORE-125-FR06: Framework reference gating ---
_framework_ref_enabled() {
  # Returns 0 (true) when framework reference is enabled, 1 (false) when disabled.
  [ "${TRW_FRAMEWORK_MD_ENABLED:-true}" != "false" ]
}

# _protocol_in_instruction_file: true when the client instruction file already
# carries the behavioral protocol.
#
# trw_instructions_sync renders the SAME protocol into both the client
# instruction file and .trw/context/behavioral_protocol.md. Clients keep their
# instruction file in context across resume, compact, and clear -- it lives in
# the system prompt, not the conversation -- so emitting the protocol again on
# those events costs ~1.3k tokens for zero new information. Emit it only when
# no instruction file carries it (light clients, bare harnesses, a project that
# has never run instructions_sync).
#
# Matches the TRW managed-block marker on a WHOLE LINE, fixed-string. Not a
# token scan: `.claude/rules/trw-mcp-python.md` §Marker/Sentinel Matching
# requires line-anchored whole-line matching for exactly this class of check,
# after a substring search once hit an inline prose mention and destroyed 705
# ROADMAP lines. A token scan for `trw_session_start` would also fire on any
# project whose instruction file merely MENTIONS the tool -- a migration note,
# a changelog entry, a README paragraph -- and then suppress the protocol while
# pointing at a section that does not exist. The marker is written only by
# trw_instructions_sync, which is the same code path that renders the protocol,
# so its presence is proof rather than correlation.
#
# Source of truth for the marker: state/claude_md/_parser.py::TRW_MARKER_START.
# Source of truth for the per-profile filename mapping:
# client_profiles/catalog.py::write_targets.instruction_path -- the list below
# is its root-file subset. A name missing here degrades to emitting the
# protocol, which is the safe direction.
_TRW_INSTRUCTION_MARKER='<!-- trw:start -->'

_protocol_in_instruction_file() {
  for _pif_f in \
    "$_project_root/CLAUDE.md" \
    "$_project_root/AGENTS.md" \
    "$_project_root/ANTIGRAVITY.md" \
    "$_project_root/.claude/INSTRUCTIONS.md" \
    "$_project_root/.codex/INSTRUCTIONS.md" \
    "$_project_root/.github/copilot-instructions.md"; do
    if [ -f "$_pif_f" ] && grep -qxF "$_TRW_INSTRUCTION_MARKER" "$_pif_f" 2>/dev/null; then
      return 0
    fi
  done
  return 1
}

# --- Value-oriented protocol summary ---
_emit_protocol() {
  if _protocol_in_instruction_file; then
    echo "PROTOCOL: unchanged and still in context — see the TRW protocol section of your client instruction file."
    return 0
  fi
  echo "## TRW Behavioral Protocol"
  echo ""
  _protocol_file="$_project_root/.trw/context/behavioral_protocol.md"
  if [ -f "$_protocol_file" ]; then
    cat "$_protocol_file"
  else
    echo "- Start: call trw_session_start() to load prior learnings and active run state"
    echo "- During: call trw_checkpoint(message) after milestones"
    echo "- Finish: call trw_deliver() to persist learnings for future sessions"
    echo "- On errors or >2 retries: call trw_learn() to record the discovery"
  fi
}

# PRD-CORE-095 FR03: Clear phase cache on all session events so the next
# UserPromptSubmit invocation always emits phase guidance.
rm -f "$_project_root/.trw/context/last_ups_phase" 2>/dev/null || true
# PRD-CORE-095 FR12: Clear injection dedup state so learnings can be re-injected.
> "$_project_root/.trw/context/injected_learning_ids.txt" 2>/dev/null || true

case "$_source" in
  startup)
    # FR01: Fresh startup — protocol table lives in CLAUDE.md (single source of truth).
    # _emit_protocol is NOT called here to avoid duplication (PRD-CORE-120-FR01).
    # It IS called for compact/clear/resume where CLAUDE.md context may be lost.
    _emit_run_state
    echo ""
    if _framework_ref_enabled; then
      echo "FRAMEWORK: Read .trw/frameworks/FRAMEWORK-CORE.md before starting work."
      echo "WHY: It defines the 6-phase execution model (RESEARCH → PLAN → IMPLEMENT → VALIDATE → REVIEW → DELIVER),"
      echo "  exit criteria for each phase, optional coordination patterns, quality gates with rubric scoring,"
      echo "  phase reversion rules, and the rationalization watchlist. Your tools implement this methodology —"
      echo "  without reading it, you will pass tool checks while missing the process that prevents rework."
      echo "  It is ~385 lines / ~8k tokens. Read it once at session start; re-read only the relevant sections at phase transitions."
      echo ""
    fi
    echo "YOUR ROLE: Verify evidence, preserve knowledge, and coordinate only when the harness and task justify it."
    echo "Delegate only for genuinely independent, parallelizable work with disjoint file ownership — not for what you could finish in a few tool calls, and not to verify your own work."
    echo ""
    echo "RIGID (never skip): trw_session_start, trw_deliver, trw_build_check, reading FRAMEWORK.md, completion artifacts."
    echo ""
    echo "Call trw_session_start(query='your task domain') to load focused learnings and any active run state."
    ;;

  resume)
    # FR02: Resume — brief, goal-oriented
    _emit_protocol
    echo ""
    _emit_run_state
    echo ""
    echo "SESSION RESUMED — your run state and learnings are preserved."
    if _framework_ref_enabled; then
      echo "FRAMEWORK: If you haven't read .trw/frameworks/FRAMEWORK-CORE.md this session, read it now — it defines exit criteria and phase gates that govern your work."
    fi
    echo "Call trw_status() to see where you left off and what to work on next."
    ;;

  compact)
    # FR03: Compaction recovery — emphasize progress is safe, show recovered state
    echo "CONTEXT COMPACTED — your conversation was compressed but your implementation progress is safe."
    echo ""
    if _framework_ref_enabled; then
      echo "FRAMEWORK RELOAD: re-read .trw/frameworks/FRAMEWORK-CORE.md before resuming work."
      echo "WHAT: per § FRAMEWORK ADHERENCE, reload the EXECUTION MODEL SUMMARY plus the phase/gate sections your"
      echo "  current phase touches — not the whole document unless the task or a governing instruction requires it."
      echo "  A full read is ~8k tokens; a targeted reload is a fraction of that and covers the gates that matter."
      echo "WHY: compaction erased your working memory of the methodology, and work without phase gates, exit"
      echo "  criteria, and quality rubrics drifts from it and needs rework."
      echo ""
    fi
    _emit_protocol
    echo ""
    # Recover pre-compaction state if available
    _state_file="$_project_root/.trw/context/pre_compact_state.json"
    if [ -f "$_state_file" ] && command -v jq >/dev/null 2>&1; then
      _run_path=$(jq -r '.run_path // empty' "$_state_file" 2>/dev/null) || true
      _phase=$(jq -r '.phase // empty' "$_state_file" 2>/dev/null) || true
      _event_count=$(jq -r '.events_logged // 0' "$_state_file" 2>/dev/null) || true
      _last_cp=$(jq -r '.last_checkpoint // empty' "$_state_file" 2>/dev/null) || true
      # Sanitize all values read from the untrusted pre_compact_state.json before
      # echoing them into the AI context (prompt-injection / control-char defense).
      _run_path=$(_sanitize_context_text "$_run_path")
      _phase=$(_sanitize_context_text "$_phase")
      _last_cp=$(_sanitize_context_text "$_last_cp")
      # events_logged must be numeric; coerce to 0 if not.
      case "$_event_count" in
        ''|*[!0-9]*) _event_count=0 ;;
      esac
      if [ -n "$_run_path" ]; then
        echo "RECOVERED: Run at $_run_path"
        [ -n "$_phase" ] && echo "RECOVERED: Phase: $_phase | Events: ${_event_count:-0}"
        [ -n "$_last_cp" ] && echo "LAST CHECKPOINT: \"$_last_cp\""
      fi
    fi
    echo ""
    echo "CONTINUE: call trw_session_start(query='your task domain') to reload learnings and active run state."
    echo "After session_start, call trw_status() if you need the current run snapshot."
    echo "Your checkpoint has your progress — pick up where you left off rather than re-planning."
    ;;

  clear)
    # FR01: Clear — full protocol injection (same as startup)
    _emit_protocol
    echo ""
    if _framework_ref_enabled; then
      echo "FRAMEWORK: Read .trw/frameworks/FRAMEWORK-CORE.md before starting work."
      echo "WHY: It defines the 6-phase execution model, exit criteria, formations, quality gates, and phase reversion"
      echo "  rules that structure model-, harness-, client-, and language-agnostic work. Your tools implement this methodology — without reading it, you will pass"
      echo "  tool checks while missing the process that prevents rework."
      echo ""
    fi
    echo "YOUR ROLE: Verify evidence, preserve knowledge, and coordinate only when the harness and task justify it."
    echo "Delegate only for genuinely independent, parallelizable work with disjoint file ownership — not for what you could finish in a few tool calls, and not to verify your own work."
    echo ""
    echo "Call trw_session_start(query='your task domain') to load focused learnings and any active run state."
    ;;

  *)
    # Fallback for unknown source
    _emit_protocol
    ;;
esac

log_hook_execution "SessionStart" "$_source" "0"

exit 0
