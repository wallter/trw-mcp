#!/bin/sh
# PRD-INFRA-002-FR07: SubagentStart hook — TRW context injection.
# Injects abbreviated TRW protocol + active run context into subagents.
# Fail-open: any error silently exits 0.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

_run_dir=$(find_active_run) || true

echo "TRW SUB-AGENT CONTEXT:"
echo "- ALWAYS call trw_recall for context; use trw_learn for discoveries; use trw_checkpoint periodically"
echo "- ALWAYS use trw_learn to record discoveries and gotchas"
echo "- ALWAYS read .trw/frameworks/FRAMEWORK.md for phase requirements"

if [ -n "$_run_dir" ]; then
  echo "- Active run: $_run_dir"
  _run_yaml="${_run_dir}meta/run.yaml"
  if [ -f "$_run_yaml" ]; then
    _phase=$(grep '^phase:' "$_run_yaml" | head -1 | sed 's/^phase:[[:space:]]*//' | tr -d "'" | tr -d '"') || true
    [ -n "$_phase" ] && echo "- Current phase: $_phase"
  fi
fi

log_hook_execution "SubagentStart" "" "0"

exit 0
