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
_allow_unverified=""
_unverified_reason=""
if command -v jq >/dev/null 2>&1; then
  _tool_name=$(printf '%s' "$_payload" | jq -r '.tool_name // empty' 2>/dev/null) || true
  _allow_unverified=$(printf '%s' "$_payload" | jq -r '.tool_input.allow_unverified // empty' 2>/dev/null) || true
  _unverified_reason=$(printf '%s' "$_payload" | jq -r '.tool_input.unverified_reason // empty' 2>/dev/null) || true
fi

# Only gate trw_deliver calls
case "$_tool_name" in
  *trw_deliver*) ;;
  *) exit 0 ;;
esac

# Check build status
_project_root="$(get_repo_root)" || exit 0
_build_status="$_project_root/.trw/context/build-status.yaml"

# PRD-CORE-214: CONSTITUTION §1.a Path-2 structured-override pass-through.
# The hook checks INTENT PRESENCE only (allow_unverified=true + a non-empty
# structured reason); record VALIDITY (schema, expiry) is adjudicated by the
# server-side gate in _deliver_gate_dispatch.py, which is the stricter,
# authoritative layer. Every other call keeps the fail-closed blocks below.
# Returns 0 (caller must then exit 0) only when override intent is present;
# appends a minimal audit line (no reason text — the server ledger is the
# authoritative record; this line exists for advisory-path visibility).
_override_passthrough() {
  _blk_class="$1"
  [ "$_allow_unverified" = "true" ] || return 1
  _reason_stripped=$(printf '%s' "$_unverified_reason" | tr -d '[:space:]') || true
  [ -n "$_reason_stripped" ] || return 1
  _audit_file="$_project_root/.trw/context/deliver-override-audit.jsonl"
  _now_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || printf 'unknown')
  _sid=$(printf '%s' "${TRW_SESSION_ID:-}" | tr -d '"\\') || true
  _scope=""
  if [ -f "$_build_status" ]; then
    _scope=$(grep '^scope:' "$_build_status" 2>/dev/null | head -1 | sed 's/^scope:[[:space:]]*//' | tr -d '"\\' | cut -c1-120) || true
  fi
  printf '{"ts":"%s","session_id":"%s","build_scope":"%s","block_class":"%s"}\n' \
    "$_now_iso" "$_sid" "$_scope" "$_blk_class" >>"$_audit_file" 2>/dev/null || true
  printf 'DELIVER-GATE: Path-2 override intent detected (allow_unverified + structured reason); passing through — the server-side gate adjudicates the AcceptableFailureRecord.\n'
  log_hook_execution "PreToolUse:deliver-gate" "$_tool_name" "0:override-passthrough:$_blk_class"
  return 0
}

if [ ! -f "$_build_status" ]; then
  # PRD-FIX-077-FR03: Fallback to .trw/context/ceremony-state.json when
  # build-status.yaml is missing (e.g., post container migration). Reads the
  # "build_check_result" and "last_build_check_ts" fields set by
  # mark_build_check() in trw_mcp/state/_ceremony_progress_state.py.
  _state_file="$_project_root/.trw/context/ceremony-state.json"
  _freshness="${TRW_BUILD_FRESHNESS_SECS:-1800}"
  case "$_freshness" in
    ''|*[!0-9]*) _freshness=1800 ;;
    *)
      if [ "$_freshness" -lt 60 ] || [ "$_freshness" -gt 86400 ]; then
        _freshness=1800
      fi
      ;;
  esac

  if [ -f "$_state_file" ] && command -v jq >/dev/null 2>&1; then
    _state_result=$(jq -r '.build_check_result // empty' "$_state_file" 2>/dev/null || printf '')
    _state_ts=$(jq -r '.last_build_check_ts // empty' "$_state_file" 2>/dev/null || printf '')

    if [ "$_state_result" = "failed" ]; then
      if _override_passthrough "fallback-failed"; then exit 0; fi
      cat >&2 <<'MSG'
BLOCKED: Build check failed — tests did not pass.

WHY: Delivering code that fails its own tests breaks the user's project.

ACTION: Fix failures, re-run project-native validation, then record only its
observed result with trw_build_check(tests_passed, test_count, failure_count,
static_checks_clean, scope).
MSG
      log_hook_execution "PreToolUse:deliver-gate" "$_tool_name" "2:build-failed"
      _trw_intentional_exit=1
      exit 2
    fi

    if [ "$_state_result" = "passed" ] && [ -n "$_state_ts" ]; then
      _now_epoch=$(date -u +%s 2>/dev/null || printf '')
      _then_epoch=$(date -u -d "$_state_ts" +%s 2>/dev/null || printf '')
      if [ -n "$_now_epoch" ] && [ -n "$_then_epoch" ]; then
        _delta=$(( _now_epoch - _then_epoch ))
        if [ "$_delta" -ge 0 ] && [ "$_delta" -le "$_freshness" ]; then
          log_hook_execution "PreToolUse:deliver-gate" "$_tool_name" "0:state-fallback-passed"
          exit 0
        fi
        if _override_passthrough "stale-build"; then exit 0; fi
        cat >&2 <<MSG
BLOCKED: Build verification is stale (${_delta}s old, window ${_freshness}s).

ACTION: Re-run project-native validation, then record its new observed result
with trw_build_check(tests_passed, test_count, failure_count,
static_checks_clean, scope) before trw_deliver.
MSG
        log_hook_execution "PreToolUse:deliver-gate" "$_tool_name" "2:stale-build"
        _trw_intentional_exit=1
        exit 2
      fi
    fi
  fi

  if _override_passthrough "no-build"; then exit 0; fi
  cat >&2 <<'MSG'
BLOCKED: No build record exists yet.

WHY: Delivering without verified tests risks shipping broken code to the user.
Unverified deliveries erode trust and cause rework — the build gate exists to
prevent exactly this.

ACTION: Run project-native validation first, then record its observed result
with trw_build_check(tests_passed, test_count, failure_count,
static_checks_clean, scope). Fix failures before retrying trw_deliver.
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
    if _override_passthrough "timeout"; then exit 0; fi
  else
    if _override_passthrough "build-failed"; then exit 0; fi
  fi

  if [ "$_timed_out" = "true" ]; then
    cat >&2 <<'MSG'
BLOCKED: Build check timed out — test results are unknown.

WHY: A timeout means the test suite did not finish within the allotted time.
Tests may have passed, but we cannot confirm. Delivering unverified code risks
shipping regressions the user will have to debug later.

ACTION: Confirm the project-native command's actual exit status, then retry the
reporter with trw_build_check(tests_passed, test_count, failure_count,
static_checks_clean, scope). The reporter has no timeout argument and does not
run validation. If the reporting service remains unavailable, report that gate
failure rather than claiming verified delivery.
MSG
  else
    cat >&2 <<'MSG'
BLOCKED: Build check failed — tests did not pass.

WHY: Delivering code that fails its own tests breaks the user's project.
The build gate prevents shipping known-broken code so the user does not have
to clean up after you.

ACTION: Read the failure details in .trw/context/build-status.yaml, fix the
failing tests, re-run project-native validation, then record its observed result
with trw_build_check(tests_passed, test_count, failure_count,
static_checks_clean, scope). Only call trw_deliver after tests pass.
MSG
  fi
  log_hook_execution "PreToolUse:deliver-gate" "$_tool_name" "2:build-failed"
  _trw_intentional_exit=1
  exit 2
fi

# Build passed — allow delivery
log_hook_execution "PreToolUse:deliver-gate" "$_tool_name" "0:build-passed"
exit 0
