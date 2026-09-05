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
# _sanitize_context_text now lives in lib-trw.sh, which this hook already
# sources. It moved there for PRD-CORE-247-NFR03: the UserPromptSubmit hook must
# apply the SAME sanitizer to the degraded-mode marker, and a second copy in the
# prompt hook is exactly the two-path-parser shape that lets one path drift
# weaker than the other. The call sites below are unchanged.

# --- PRD-CORE-125-FR06 + PRD-CORE-247-FR07: Framework reference gating ---
#
# Two gates, not one. TRW_FRAMEWORK_MD_ENABLED is the operator switch. The
# degraded-mode latch is the truthfulness gate PRD-CORE-247-FR07 adds: while
# the MCP surface is known-absent, the document describes tools that do not
# exist in this session, and charging an estimated 9,230 tokens for methodology
# the agent cannot apply is the exact "full instruction cost, zero capability"
# case the submission named. A stale lib-trw.sh that predates the latch has no
# `trw_degraded_latched`, which resolves to "emit" — the safe direction.
_framework_ref_enabled() {
  [ "${TRW_FRAMEWORK_MD_ENABLED:-true}" != "false" ] || return 1
  if command -v trw_degraded_latched >/dev/null 2>&1 && trw_degraded_latched "$_payload_session_id"; then
    return 1
  fi
  return 0
}

# --- PRD-CORE-247-FR07: phase-scoped framework read ---
#
# The whole document measures 35,073 characters (9,230 estimated tokens at the
# 3.8-chars-per-token estimator used throughout PRD-CORE-247). Naming only the
# sections the current phase needs costs at most 6,349 — the `plan` row, 18.1
# percent of the document. Measured per section against
# .trw/frameworks/FRAMEWORK-CORE.md: EXECUTION MODEL SUMMARY 2,242, PHASES
# 1,824, CEREMONY TIERS 2,283, RIGID / FLEXIBLE 2,236, GATES 1,644.
#
# This is a documented table, not a magic constant, and it is TOTAL over every
# value infer_phase can return (none, early, plan, implement, validate, deliver,
# done) — an unmapped phase falls to the `none` row rather than to the whole
# document. `framework_read_scope: full` in .trw/config.yaml restores the
# whole-document read; it is an operator lever for the unmeasured adherence
# question (PRD-CORE-247 OQ-03), not an on/off switch for the fix.
_framework_scope() {
  _fs_val="${TRW_FRAMEWORK_READ_SCOPE:-}"
  if [ -z "$_fs_val" ] && [ -f "$_project_root/.trw/config.yaml" ]; then
    _fs_val=$(grep -m1 '^framework_read_scope:' "$_project_root/.trw/config.yaml" 2>/dev/null \
      | sed 's/^framework_read_scope:[[:space:]]*//' | tr -d "'\"" | tr -d '[:space:]') || _fs_val=""
  fi
  case "$_fs_val" in
    full) printf 'full' ;;
    *) printf 'phase' ;;
  esac
}

_framework_sections_for_phase() {
  case "$1" in
    plan)
      printf 'EXECUTION MODEL SUMMARY, PHASES, CEREMONY TIERS' ;;
    implement)
      printf 'EXECUTION MODEL SUMMARY, PHASES, RIGID / FLEXIBLE TOOL CLASSIFICATION' ;;
    validate | review)
      printf 'EXECUTION MODEL SUMMARY, GATES, RIGID / FLEXIBLE TOOL CLASSIFICATION' ;;
    deliver | done)
      printf 'EXECUTION MODEL SUMMARY, GATES' ;;
    *)
      printf 'EXECUTION MODEL SUMMARY, PHASES' ;;
  esac
}

# _emit_framework_directive: the single framework-read emitter for every source.
# Args: $1=lead prefix (e.g. "FRAMEWORK" / "FRAMEWORK RELOAD"),
#       $2=1 to append the WHY paragraph, 0 to omit it.
#
# `resume` omits WHY deliberately. The rationale is the most repeated text this
# hook emits and resume is the one source where the agent has already read it
# THIS session; the byte budget in tests/test_session_start_hook.py is what
# keeps that decision honest.
# _own_phase: THIS session's phase, or "none".
#
# Deliberately NOT infer_phase. PRD-UF-047 pinned that infer_phase launders
# recency — when the session is unpinned it falls back to find_active_run and
# returns whichever run sorted newest project-wide, which with several instances
# live is routinely another session's. Scoping the framework read to a foreign
# run's phase would name the wrong sections with no signal that it had. Resolving
# the owned run and reading its own event ladder is the same discipline
# phase-cycle-stop.sh uses; no owned run means "none", the honest answer, whose
# row is the broadest (EXECUTION MODEL SUMMARY + PHASES).
_own_phase() {
  command -v resolve_owned_run >/dev/null 2>&1 || { printf 'none'; return; }
  command -v phase_from_events >/dev/null 2>&1 || { printf 'none'; return; }
  _op_dir=$(resolve_owned_run "$_payload_session_id" 2>/dev/null) || _op_dir=""
  [ -n "$_op_dir" ] || { printf 'none'; return; }
  phase_from_events "${_op_dir}meta/events.jsonl"
}

_emit_framework_directive() {
  _efd_phase=$(_own_phase) || _efd_phase="none"
  [ -n "$_efd_phase" ] || _efd_phase="none"
  if [ "$(_framework_scope)" = "full" ]; then
    echo "$1: Read .trw/frameworks/FRAMEWORK-CORE.md — 393 lines / 35,073 characters / ~9,230 tokens."
    echo "  (whole-document read, selected by framework_read_scope: full)"
  else
    echo "$1: Read these .trw/frameworks/FRAMEWORK-CORE.md sections for your phase (${_efd_phase}):"
    echo "  $(_framework_sections_for_phase "$_efd_phase")"
    echo "  At most ~6,349 characters, not the whole 35,073-character document. Re-read at each phase transition."
  fi
  [ "${2:-1}" = "1" ] || return 0
  echo "WHY: it defines the 6-phase execution model, per-phase exit criteria, quality gates with rubric"
  echo "  scoring, phase reversion rules, and the rationalization watchlist. Your tools implement this"
  echo "  methodology - without it you pass tool checks while missing the process that prevents rework."
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

# PRD-CORE-247-FR01: stamp this session's epoch, and make no claim about whether
# the MCP surface attached. This hook CANNOT know: it fires at session start and
# the reported client gave up on the connection 120 seconds later. Detection is
# observational and happens in UserPromptSubmit; all this does is give that
# detector a "since when" to measure against. Cost is one small file write.
#
# `startup` is the only genuinely-new session, so it is the only source that
# writes the epoch or clears the "already told them" latch.
#
# Review follow-up: this used to write the marker on EVERY source, which
# contradicted its own purpose. resume/compact/clear happen INSIDE a session
# whose transport state has not changed, and rewriting the marker there resets
# both halves of the FR01 verdict -- the elapsed-time clock back to zero and the
# prompt counter back to 0. A session that had accumulated 170 s of a 180 s grace
# window and one prompt lost both to a compaction, and a real outage stayed
# undetected for another full window. Writing only on `startup` is what makes
# "elapsed since this session began" mean what it says.
#
# A missing marker (a resume in a project that has never seen a startup) is the
# fail-open case: trw_session_epoch_ts returns non-zero, the detector concludes
# the surface is present, and nothing is emitted.
# PRD-FIX-128-FR02/FR03: both markers are keyed on THIS session's identity, so
# the payload session id is threaded through. Without it, a client that exports
# no session variable (every profile but claude-code) resolves no key at all,
# and the honest consequence is that no marker is written and no claim is ever
# made about this session.
if [ "$_source" = "startup" ] && command -v trw_write_session_epoch >/dev/null 2>&1; then
  trw_write_session_epoch "$_payload_session_id"
  trw_clear_degraded_latch "$_payload_session_id"
fi

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
      _emit_framework_directive "FRAMEWORK" 1
      echo ""
    fi
    echo "YOUR ROLE: Verify evidence, preserve knowledge, and coordinate only when the harness and task justify it."
    echo "Delegate only for genuinely independent, parallelizable work with disjoint file ownership — not for what you could finish in a few tool calls, and not to verify your own work."
    echo ""
    # PRD-CORE-247-FR02: RIGID names an OBLIGATION, not a tool call. Saying so
    # here is what makes the offline substitute table legible as a transfer of
    # the same obligation rather than as permission to skip it.
    echo "RIGID (never skip): trw_session_start, trw_deliver, trw_build_check, reading FRAMEWORK-CORE.md, completion artifacts."
    echo "  These name OBLIGATIONS, not tool calls. If the trw_ tools are unreachable, each one transfers to"
    echo "  its 'trw-mcp local ...' equivalent (run 'trw-mcp local' for the list) — it does not lapse."
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
      _emit_framework_directive "FRAMEWORK" 0
    fi
    echo "Call trw_status() to see where you left off and what to work on next."
    ;;

  compact)
    # FR03: Compaction recovery — emphasize progress is safe, show recovered state
    echo "CONTEXT COMPACTED — your conversation was compressed but your implementation progress is safe."
    echo ""
    if _framework_ref_enabled; then
      _emit_framework_directive "FRAMEWORK RELOAD" 1
      echo "  Compaction erased your working memory of the methodology, and work without phase gates, exit"
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
      _emit_framework_directive "FRAMEWORK" 1
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
