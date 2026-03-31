#!/bin/sh
# PRD-INFRA-024-FR02 + PRD-CORE-095-FR01-FR06: Phase-aware UserPromptSubmit hook.
# Emits calibrated context per execution phase. Fail-open: never blocks prompts.
# Output target: <150 tokens (~600 chars) per phase. "done" phase: 0 tokens.
#
# PRD-CORE-095: Phase-change suppression — caches last emitted phase in
# .trw/context/last_ups_phase. Same-phase messages produce no output.
# "none" phase always emits (FR04). "done" phase is always silent (FR06).
#
# Performance: ~71ms avg latency (benchmarked 2026-03-29, 3 runs).
# Fires before every user prompt. Primary cost: infer_phase() scanning events.jsonl.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

# Read stdin (required by hook contract — UserPromptSubmit sends JSON with prompt field)
cat >/dev/null 2>&1 || true

_phase=$(infer_phase)

# --- PRD-CORE-095 FR01-FR06: Phase-change suppression ---
_project_root="$(get_repo_root)" || exit 0
_phase_cache="$_project_root/.trw/context/last_ups_phase"

# FR04: "none" phase always emits (agent needs session_start reminder)
# FR06: "done" phase is always silent
if [ "$_phase" != "none" ]; then
  _cached_phase=""
  if [ -f "$_phase_cache" ]; then
    _cached_phase=$(cat "$_phase_cache" 2>/dev/null) || true
  fi
  # FR02: Same-phase suppression — skip output if phase unchanged
  if [ "$_cached_phase" = "$_phase" ]; then
    log_hook_execution "UserPromptSubmit" "$_phase" "0" "cached"
    exit 0
  fi
  # FR01: Write current phase to cache (atomic write)
  printf '%s' "$_phase" > "$_phase_cache" 2>/dev/null || true
fi

case "$_phase" in
  none)
    echo "TRW: Call trw_session_start(query='your task domain') to load context, then read .trw/frameworks/FRAMEWORK.md — it defines the methodology your tools implement."
    ;;
  early)
    echo "TRW [RESEARCH/PLAN]: PRD validation gates implementation — trw_prd_validate catches ambiguity before it becomes rework."
    ;;
  plan)
    echo "TRW [PLAN]: Run trw_prd_validate before implementing — catching spec gaps now saves 2-3x rework vs discovering them during implementation."
    ;;
  implement)
    echo "TRW [IMPLEMENT]: Before completing, re-read FRs for coverage gaps. Call trw_checkpoint after milestones — uncheckpointed work is lost on compaction."
    ;;
  validate)
    echo "TRW [VALIDATE]: trw_build_check(scope='full') is required — pytest alone doesn't satisfy the gate."
    ;;
  deliver)
    echo "TRW [DELIVER]: trw_deliver() persists learnings, syncs CLAUDE.md, and closes the run — without it, your session's work is invisible to future agents."
    ;;
  done)
    # Silent — run is complete, no output (FR06)
    ;;
esac

log_hook_execution "UserPromptSubmit" "$_phase" "0"
exit 0
