#!/bin/sh
# PRD-INFRA-002-FR08: PostToolUse hook — phase transition context.
# Fires after trw_phase_check. Emits a reminder to re-read FRAMEWORK.md
# when a phase check passes.
#
# Fail-open: any error silently exits 0.
# Dependencies: POSIX shell. jq optional.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

# Read JSON payload from stdin
_payload=$(cat) || exit 0

# Extract pass/fail from tool output
_passed=""
if command -v jq >/dev/null 2>&1; then
  _passed=$(printf '%s' "$_payload" | jq -r '.tool_output.passed // empty' 2>/dev/null) || true
  # Also check string content for "passed": true
  if [ -z "$_passed" ]; then
    _passed=$(printf '%s' "$_payload" | jq -r '.tool_output.content // empty' 2>/dev/null | grep -o '"passed"[[:space:]]*:[[:space:]]*true' | head -1) || true
    [ -n "$_passed" ] && _passed="true"
  fi
fi

# Fallback: grep for passed
if [ -z "$_passed" ]; then
  _passed=$(printf '%s' "$_payload" | grep -o '"passed"[[:space:]]*:[[:space:]]*true' | head -1) || true
  [ -n "$_passed" ] && _passed="true"
fi

if [ "$_passed" = "true" ]; then
  echo "PHASE CHECK PASSED: Re-read .trw/frameworks/FRAMEWORK.md for the new phase requirements. Execute trw_recall with relevant query for phase-specific learnings."
fi

log_hook_execution "PostToolUse" "mcp__trw__trw_phase_check" "0"

exit 0
