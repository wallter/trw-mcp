#!/usr/bin/env bash
# Shell-level test for stop-ceremony hook's trw-loop worker bypass seam.
# The hook must fail open (exit 0) when TRW_LOOP_WORKER is set, while preserving
# normal trw_deliver enforcement (exit 2) for non-loop sessions.
set -u

_fail=0
_pass=0

_here="$(cd "$(dirname "$0")" && pwd)"
_hook="$_here/../../src/trw_mcp/data/hooks/stop-ceremony.sh"

if [ ! -r "$_hook" ]; then
  echo "FAIL: cannot find hook at $_hook"
  exit 1
fi

# Build a project with an active run that has logged events but no deliver,
# i.e. the state that normally triggers a Stop block.
_setup_project() {
  _p=$(mktemp -d)
  _run="$_p/.trw/runs/sometask/20260101T000000Z-abcd0000"
  mkdir -p "$_run/meta" "$_p/.trw/context"
  printf 'task: sometask\n' >"$_run/meta/run.yaml"
  printf '{"ts":"2026-01-01T00:00:00Z","event":"file_modified"}\n' >"$_run/meta/events.jsonl"
  printf '%s' "$_p"
}

# --- Test 1: non-loop session with events + no deliver → exit 2 (enforced) ---
_t1=$(_setup_project)
CLAUDE_PROJECT_DIR="$_t1" sh "$_hook" </dev/null >/dev/null 2>&1
_rc=$?
if [ $_rc -eq 2 ]; then
  _pass=$((_pass + 1)); echo "PASS: non-loop session is still enforced (exit 2)"
else
  _fail=$((_fail + 1)); echo "FAIL: non-loop session exited $_rc (expected 2)"
fi
rm -rf "$_t1"

# --- Test 2: same state, TRW_LOOP_WORKER=1 → exit 0 (bypass) ---
_t2=$(_setup_project)
TRW_LOOP_WORKER=1 CLAUDE_PROJECT_DIR="$_t2" sh "$_hook" </dev/null >/dev/null 2>&1
_rc=$?
if [ $_rc -eq 0 ]; then
  _pass=$((_pass + 1)); echo "PASS: loop worker bypasses enforcement (exit 0)"
else
  _fail=$((_fail + 1)); echo "FAIL: loop worker exited $_rc (expected 0)"
fi
# The bypass must short-circuit before the block counter is touched.
if [ ! -f "$_t2/.trw/context/stop_block_count" ]; then
  _pass=$((_pass + 1)); echo "PASS: bypass leaves block counter untouched"
else
  _fail=$((_fail + 1)); echo "FAIL: bypass wrote stop_block_count"
fi
rm -rf "$_t2"

# --- Test 3: bypass logs an auditable execution line ---
_t3=$(_setup_project)
TRW_LOOP_WORKER=1 CLAUDE_PROJECT_DIR="$_t3" sh "$_hook" </dev/null >/dev/null 2>&1
if grep -q 'matcher=loop-worker-bypass' "$_t3/.trw/context/hook-executions.log" 2>/dev/null; then
  _pass=$((_pass + 1)); echo "PASS: bypass is recorded in hook-executions.log"
else
  _fail=$((_fail + 1)); echo "FAIL: bypass not recorded in hook-executions.log"
fi
rm -rf "$_t3"

# --- Test 4: empty marker is treated as not-a-loop-worker → exit 2 ---
_t4=$(_setup_project)
TRW_LOOP_WORKER="" CLAUDE_PROJECT_DIR="$_t4" sh "$_hook" </dev/null >/dev/null 2>&1
_rc=$?
if [ $_rc -eq 2 ]; then
  _pass=$((_pass + 1)); echo "PASS: empty marker does not bypass (exit 2)"
else
  _fail=$((_fail + 1)); echo "FAIL: empty marker exited $_rc (expected 2)"
fi
rm -rf "$_t4"

# --- Test 5: non-one marker is not accepted as loop worker authority → exit 2 ---
_t5=$(_setup_project)
TRW_LOOP_WORKER=maybe CLAUDE_PROJECT_DIR="$_t5" sh "$_hook" </dev/null >/dev/null 2>&1
_rc=$?
if [ $_rc -eq 2 ]; then
  _pass=$((_pass + 1)); echo "PASS: non-one marker does not bypass (exit 2)"
else
  _fail=$((_fail + 1)); echo "FAIL: non-one marker exited $_rc (expected 2)"
fi
rm -rf "$_t5"

echo ""
echo "=== $_pass passed, $_fail failed ==="
[ $_fail -eq 0 ]
