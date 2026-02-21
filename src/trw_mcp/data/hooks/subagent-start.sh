#!/bin/sh
# PRD-INFRA-002-FR07: SubagentStart hook — TRW context injection.
# Injects abbreviated TRW protocol + active run context into subagents.
# Includes phase-specific guidance so shards know what's expected.
# Fail-open: any error silently exits 0.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

_run_dir=$(find_active_run) || true
_phase=""

echo "TRW SUB-AGENT CONTEXT:"
echo "- Call trw_recall for relevant prior learnings before starting work"
echo "- Call trw_learn to record discoveries and gotchas for future sessions"
echo "- Call trw_checkpoint after each milestone with a summary of what you completed"

if [ -n "$_run_dir" ]; then
  echo "- Active run: $_run_dir"
  _run_yaml="${_run_dir}meta/run.yaml"
  if [ -f "$_run_yaml" ]; then
    _phase=$(grep '^phase:' "$_run_yaml" | head -1 | sed 's/^phase:[[:space:]]*//' | tr -d "'" | tr -d '"') || true
    [ -n "$_phase" ] && echo "- Current phase: $_phase"
  fi
fi

# Phase-specific guidance — agents skip VALIDATE→REVIEW without explicit reminders
case "$_phase" in
  implement*)
    echo ""
    echo "BEFORE COMPLETING YOUR WORK — self-review checklist:"
    echo "  1. Re-read your assigned PRD FRs — verify EVERY requirement is implemented"
    echo "  2. Check integration — new code must be imported and called from existing code"
    echo "  3. Review your diff for DRY/KISS/SOLID quality"
    echo "  4. Run trw_build_check(scope='full') to confirm pytest + mypy pass"
    echo "  5. Write a completion summary in trw_checkpoint: FRs implemented, tests, integration points"
    echo "Skipping self-review creates rework — doing it now saves the project a full extra pass."
    ;;
  validate*)
    echo ""
    echo "VALIDATE PHASE: Run trw_build_check(scope='full'). Verify coverage >= target. Check for P0 findings."
    ;;
  review*)
    echo ""
    echo "REVIEW PHASE: Review the diff for quality (DRY/KISS/SOLID). Fix incomplete integrations. Record learnings."
    ;;
esac

log_hook_execution "SubagentStart" "" "0"

exit 0
