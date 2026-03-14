#!/bin/sh
# PRD-INFRA-038-FR01: PreToolUse gate — blocks trw_deliver when build-check hasn't passed.
# Matcher: mcp__trw__trw_deliver
# Exit 2 = block tool call with feedback. Exit 0 = allow.
# Fail-open: infrastructure errors exit 0.
set -e
_trw_intentional_exit=0
trap '[ "$_trw_intentional_exit" = "1" ] || exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

# Read stdin payload
_payload=$(cat) || exit 0
_tool_name=""
if command -v jq >/dev/null 2>&1; then
  _tool_name=$(printf '%s' "$_payload" | jq -r '.tool_name // empty' 2>/dev/null) || true
fi

# Only gate trw_deliver calls
case "$_tool_name" in
  *trw_deliver*) ;;
  *) exit 0 ;;
esac

# Check build status
_project_root="$(get_repo_root)" || exit 0
_build_status="$_project_root/.trw/context/build-status.yaml"

if [ ! -f "$_build_status" ]; then
  echo "TRW GATE: Run trw_build_check(scope='full') before calling trw_deliver — no build record found." >&2
  log_hook_execution "PreToolUse:deliver-gate" "$_tool_name" "2:no-build"
  _trw_intentional_exit=1
  exit 2
fi

_tests_passed=""
_tests_passed=$(grep '^tests_passed:' "$_build_status" 2>/dev/null | head -1 | sed 's/^tests_passed:[[:space:]]*//' | tr -d "'" | tr -d '"') || true

if [ "$_tests_passed" != "true" ]; then
  echo "TRW GATE: Build check failed or not run. Run trw_build_check(scope='full') and fix failures before delivery." >&2
  log_hook_execution "PreToolUse:deliver-gate" "$_tool_name" "2:build-failed"
  _trw_intentional_exit=1
  exit 2
fi

# Build passed — allow delivery
log_hook_execution "PreToolUse:deliver-gate" "$_tool_name" "0:build-passed"
exit 0
