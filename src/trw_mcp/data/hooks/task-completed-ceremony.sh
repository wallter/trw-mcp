#!/bin/sh
# PRD-INFRA-004-FR01: TaskCompleted hook — soft quality gate.
# Logs completion events. Warns about incomplete ceremony but does NOT block.
# Ceremony enforcement is handled by the Stop hook (stop-ceremony.sh).
# Subagent task completions should never be blocked — they are intermediate steps.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

# Read stdin payload for logging
_payload=$(cat) || exit 0
_task_subject=""
_teammate_name=""
if command -v jq >/dev/null 2>&1; then
  _task_subject=$(printf '%s' "$_payload" | jq -r '.task_subject // empty' 2>/dev/null) || true
  _teammate_name=$(printf '%s' "$_payload" | jq -r '.teammate_name // empty' 2>/dev/null) || true
fi

# Fallback extraction without jq
if [ -z "$_task_subject" ]; then
  _task_subject=$(printf '%s' "$_payload" | grep -o '"task_subject"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"task_subject"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
fi

# Log the completion event (informational only)
log_hook_execution "TaskCompleted" "${_teammate_name:-unknown}:${_task_subject}" "0"

# Soft check: warn about incomplete ceremony but allow completion
_missing=$(check_ceremony_status 2>/dev/null) || exit 0
if [ -n "$_missing" ]; then
  echo "TRW NOTE: Ceremony pending — remember to call trw_deliver() when all tasks are done." >&2
fi

exit 0
