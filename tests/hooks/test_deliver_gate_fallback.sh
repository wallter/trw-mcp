#!/usr/bin/env bash
# PRD-FIX-077-FR03: Shell-level test for deliver-gate hook's ceremony-state fallback.
set -u

_fail=0
_pass=0

_here="$(cd "$(dirname "$0")" && pwd)"
_hook="$_here/../../src/trw_mcp/data/hooks/pre-tool-deliver-gate.sh"

if [ ! -x "$_hook" ] && [ ! -r "$_hook" ]; then
  echo "FAIL: cannot find hook at $_hook"
  exit 1
fi

_run_hook() {
  # $1 = project_root, stdin = payload
  CLAUDE_PROJECT_DIR="$1" sh "$_hook"
}

_payload='{"tool_name":"mcp__trw__trw_deliver"}'

_setup_project() {
  _p=$(mktemp -d)
  mkdir -p "$_p/.trw/context"
  printf '%s' "$_p"
}

# --- Test 1: build-status.yaml present, passed → exit 0 (regression) ---
_t1=$(_setup_project)
cat >"$_t1/.trw/context/build-status.yaml" <<EOF
tests_passed: true
timed_out: false
EOF
printf '%s' "$_payload" | _run_hook "$_t1" >/dev/null 2>&1
_rc=$?
if [ $_rc -eq 0 ]; then
  _pass=$((_pass + 1)); echo "PASS: primary build-status.yaml passed path"
else
  _fail=$((_fail + 1)); echo "FAIL: primary path exited $_rc"
fi
rm -rf "$_t1"

# --- Test 2: missing build-status + fresh ceremony-state passed → exit 0 ---
_t2=$(_setup_project)
_now_iso=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)
cat >"$_t2/.trw/context/ceremony-state.json" <<EOF
{"build_check_result":"passed","last_build_check_ts":"$_now_iso"}
EOF
printf '%s' "$_payload" | _run_hook "$_t2" >/dev/null 2>&1
_rc=$?
if [ $_rc -eq 0 ]; then
  _pass=$((_pass + 1)); echo "PASS: ceremony-state fallback (fresh passed)"
else
  _fail=$((_fail + 1)); echo "FAIL: fresh fallback exited $_rc (expected 0)"
fi
rm -rf "$_t2"

# --- Test 3: missing build-status + stale ceremony-state passed → exit 2 ---
_t3=$(_setup_project)
_stale_iso=$(date -u -d '3 hours ago' +%Y-%m-%dT%H:%M:%S+00:00 2>/dev/null)
if [ -n "$_stale_iso" ]; then
  cat >"$_t3/.trw/context/ceremony-state.json" <<EOF
{"build_check_result":"passed","last_build_check_ts":"$_stale_iso"}
EOF
  printf '%s' "$_payload" | _run_hook "$_t3" >/dev/null 2>&1
  _rc=$?
  if [ $_rc -eq 2 ]; then
    _pass=$((_pass + 1)); echo "PASS: stale ceremony-state blocks"
  else
    _fail=$((_fail + 1)); echo "FAIL: stale fallback exited $_rc (expected 2)"
  fi
else
  echo "SKIP: date -d not supported"
fi
rm -rf "$_t3"

# --- Test 4: missing build-status + failed ceremony-state → exit 2 ---
_t4=$(_setup_project)
_now_iso=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)
cat >"$_t4/.trw/context/ceremony-state.json" <<EOF
{"build_check_result":"failed","last_build_check_ts":"$_now_iso"}
EOF
printf '%s' "$_payload" | _run_hook "$_t4" >/dev/null 2>&1
_rc=$?
if [ $_rc -eq 2 ]; then
  _pass=$((_pass + 1)); echo "PASS: failed ceremony-state blocks"
else
  _fail=$((_fail + 1)); echo "FAIL: failed fallback exited $_rc (expected 2)"
fi
rm -rf "$_t4"

# --- Test 5: missing build-status + missing ceremony-state → exit 2 ---
_t5=$(_setup_project)
printf '%s' "$_payload" | _run_hook "$_t5" >/dev/null 2>&1
_rc=$?
if [ $_rc -eq 2 ]; then
  _pass=$((_pass + 1)); echo "PASS: no-build blocks"
else
  _fail=$((_fail + 1)); echo "FAIL: no-build exited $_rc (expected 2)"
fi
rm -rf "$_t5"

# --- Test 6: TRW_BUILD_FRESHNESS_SECS=300 honored ---
_t6=$(_setup_project)
_old_iso=$(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%S+00:00 2>/dev/null)
if [ -n "$_old_iso" ]; then
  cat >"$_t6/.trw/context/ceremony-state.json" <<EOF
{"build_check_result":"passed","last_build_check_ts":"$_old_iso"}
EOF
  printf '%s' "$_payload" | TRW_BUILD_FRESHNESS_SECS=300 CLAUDE_PROJECT_DIR="$_t6" sh "$_hook" >/dev/null 2>&1
  _rc=$?
  if [ $_rc -eq 2 ]; then
    _pass=$((_pass + 1)); echo "PASS: TRW_BUILD_FRESHNESS_SECS=300 rejects 10-min-old state"
  else
    _fail=$((_fail + 1)); echo "FAIL: env-var freshness exited $_rc (expected 2)"
  fi
else
  echo "SKIP: date -d not supported"
fi
rm -rf "$_t6"

echo ""
echo "=== $_pass passed, $_fail failed ==="
[ $_fail -eq 0 ]
