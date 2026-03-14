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
  cat >&2 <<'MSG'
BLOCKED: No build record exists yet.

WHY: Delivering without verified tests risks shipping broken code to the user.
Unverified deliveries erode trust and cause rework — the build gate exists to
prevent exactly this.

ACTION: Call trw_build_check(scope='full') first, fix any failures, then retry
trw_deliver.
MSG
  log_hook_execution "PreToolUse:deliver-gate" "$_tool_name" "2:no-build"
  _trw_intentional_exit=1
  exit 2
fi

_tests_passed=""
_tests_passed=$(grep '^tests_passed:' "$_build_status" 2>/dev/null | head -1 | sed 's/^tests_passed:[[:space:]]*//' | tr -d "'" | tr -d '"') || true

if [ "$_tests_passed" != "true" ]; then
  # Check if it was a timeout (not an actual failure)
  _timed_out=""
  _timed_out=$(grep '^timed_out:' "$_build_status" 2>/dev/null | head -1 | sed 's/^timed_out:[[:space:]]*//' | tr -d "'" | tr -d '"') || true

  if [ "$_timed_out" = "true" ]; then
    cat >&2 <<'MSG'
BLOCKED: Build check timed out — test results are unknown.

WHY: A timeout means the test suite did not finish within the allotted time.
Tests may have passed, but we cannot confirm. Delivering unverified code risks
shipping regressions the user will have to debug later.

ACTION (pick one):
  1. Re-run with a longer timeout: trw_build_check(scope='full', timeout_secs=600)
  2. If you already ran tests manually via Bash and they passed, tell the user
     the build gate timed out and ask them to approve delivery.
MSG
  else
    cat >&2 <<'MSG'
BLOCKED: Build check failed — tests did not pass.

WHY: Delivering code that fails its own tests breaks the user's project.
The build gate prevents shipping known-broken code so the user does not have
to clean up after you.

ACTION: Read the failure details in .trw/context/build-status.yaml, fix the
failing tests, then re-run trw_build_check(scope='full'). Only call
trw_deliver after tests pass.
MSG
  fi
  log_hook_execution "PreToolUse:deliver-gate" "$_tool_name" "2:build-failed"
  _trw_intentional_exit=1
  exit 2
fi

# Build passed — allow delivery
log_hook_execution "PreToolUse:deliver-gate" "$_tool_name" "0:build-passed"
exit 0
