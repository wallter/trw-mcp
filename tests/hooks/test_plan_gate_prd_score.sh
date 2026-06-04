#!/usr/bin/env bash
# F3 fix: PLAN->IMPLEMENT gate must enforce the PRD quality SCORE, not just
# that trw_prd_validate was called. A PRD scoring < 60 (REVIEW tier) must
# BLOCK (exit 2); a PRD scoring >= 60 must ALLOW (exit 0). Missing/unreadable
# cache falls back to call-presence (exit 0) so we never block on telemetry gaps.
set -u

_fail=0
_pass=0

_here="$(cd "$(dirname "$0")" && pwd)"
_hook="$_here/../../src/trw_mcp/data/hooks/phase-cycle-stop.sh"

if [ ! -r "$_hook" ]; then
  echo "FAIL: cannot find hook at $_hook"
  exit 1
fi

# Build a tmp project with an active run in the PLAN phase:
#   - meta/run.yaml so find_active_run() locates it
#   - meta/events.jsonl with a trw_prd_validate event (no later-phase events),
#     so infer_phase() returns "plan"
# $1 = optional total_score to seed into prd-validation.yaml ("" = no cache file)
_setup_plan_project() {
  _score="${1:-}"
  _p=$(mktemp -d)
  mkdir -p "$_p/.trw/context"
  mkdir -p "$_p/.trw/cache"
  _run="$_p/.trw/runs/sample-task/20260604T000000Z-deadbeef"
  mkdir -p "$_run/meta"
  printf 'run_id: 20260604T000000Z-deadbeef\nphase: plan\n' > "$_run/meta/run.yaml"
  printf '%s\n' '{"ts":"2026-06-04T00:00:00Z","event":"tool_invocation","tool_name":"trw_prd_validate","success":true}' \
    > "$_run/meta/events.jsonl"
  if [ -n "$_score" ]; then
    # Minimal cache shaped like the real prd-validation.yaml: a hash-keyed map
    # whose entry carries a two-space-indented total_score line.
    cat > "$_p/.trw/cache/prd-validation.yaml" <<EOF
0000000000000000000000000000000000000000000000000000000000000000:
  path: /tmp/PRD-FIX-999.md
  valid: false
  total_score: ${_score}
  quality_tier: draft
EOF
  fi
  printf '%s' "$_p"
}

_run_hook() {
  # $1 = project_root
  CLAUDE_PROJECT_DIR="$1" sh "$_hook" < /dev/null
}

# --- Test 1: low score (24) BLOCKS the PLAN gate (exit 2) — the core F3 defect ---
_t1=$(_setup_plan_project "24.0")
_out=$(_run_hook "$_t1" 2>&1); _rc=$?
if [ $_rc -eq 2 ]; then
  case "$_out" in
    *"below the required"*|*"24"*)
      _pass=$((_pass + 1)); echo "PASS: low score (24) blocks PLAN gate with score-aware message" ;;
    *)
      _pass=$((_pass + 1)); echo "PASS: low score (24) blocks PLAN gate (exit 2)" ;;
  esac
else
  _fail=$((_fail + 1)); echo "FAIL: low score (24) exited $_rc (expected 2). out=$_out"
fi
rm -rf "$_t1"

# --- Test 2: just-below threshold (59.99) BLOCKS (exit 2) ---
_t2=$(_setup_plan_project "59.99")
_run_hook "$_t2" >/dev/null 2>&1; _rc=$?
if [ $_rc -eq 2 ]; then
  _pass=$((_pass + 1)); echo "PASS: 59.99 (just below 60) blocks PLAN gate"
else
  _fail=$((_fail + 1)); echo "FAIL: 59.99 exited $_rc (expected 2)"
fi
rm -rf "$_t2"

# --- Test 3: at threshold (60.0) ALLOWS (exit 0) ---
_t3=$(_setup_plan_project "60.0")
_run_hook "$_t3" >/dev/null 2>&1; _rc=$?
if [ $_rc -eq 0 ]; then
  _pass=$((_pass + 1)); echo "PASS: 60.0 (REVIEW tier) allows PLAN gate"
else
  _fail=$((_fail + 1)); echo "FAIL: 60.0 exited $_rc (expected 0)"
fi
rm -rf "$_t3"

# --- Test 4: high score (88.5) ALLOWS (exit 0) ---
_t4=$(_setup_plan_project "88.5")
_run_hook "$_t4" >/dev/null 2>&1; _rc=$?
if [ $_rc -eq 0 ]; then
  _pass=$((_pass + 1)); echo "PASS: 88.5 allows PLAN gate"
else
  _fail=$((_fail + 1)); echo "FAIL: 88.5 exited $_rc (expected 0)"
fi
rm -rf "$_t4"

# --- Test 5: validate event present but NO cache file -> safe default ALLOW ---
_t5=$(_setup_plan_project "")
_run_hook "$_t5" >/dev/null 2>&1; _rc=$?
if [ $_rc -eq 0 ]; then
  _pass=$((_pass + 1)); echo "PASS: missing cache falls back to call-presence (allow)"
else
  _fail=$((_fail + 1)); echo "FAIL: missing cache exited $_rc (expected 0)"
fi
rm -rf "$_t5"

# --- Test 6: TRW_MIN_PRD_SCORE override raises the bar (80) so 70 now BLOCKS ---
_t6=$(_setup_plan_project "70.0")
CLAUDE_PROJECT_DIR="$_t6" TRW_MIN_PRD_SCORE=80 sh "$_hook" < /dev/null >/dev/null 2>&1; _rc=$?
if [ $_rc -eq 2 ]; then
  _pass=$((_pass + 1)); echo "PASS: TRW_MIN_PRD_SCORE=80 blocks a score-70 PRD"
else
  _fail=$((_fail + 1)); echo "FAIL: TRW_MIN_PRD_SCORE=80 with score 70 exited $_rc (expected 2)"
fi
rm -rf "$_t6"

# --- Test 7: last-entry semantics — a low score AFTER a high one BLOCKS ---
# (proves we read the most-recently-appended entry, not "any passing entry")
_t7=$(_setup_plan_project "")
cat > "$_t7/.trw/cache/prd-validation.yaml" <<EOF
1111111111111111111111111111111111111111111111111111111111111111:
  path: /tmp/PRD-OLD-001.md
  total_score: 92.0
  quality_tier: approved
2222222222222222222222222222222222222222222222222222222222222222:
  path: /tmp/PRD-NEW-002.md
  total_score: 31.0
  quality_tier: draft
EOF
_run_hook "$_t7" >/dev/null 2>&1; _rc=$?
if [ $_rc -eq 2 ]; then
  _pass=$((_pass + 1)); echo "PASS: latest entry (31) blocks even when an earlier entry (92) passed"
else
  _fail=$((_fail + 1)); echo "FAIL: last-entry low score exited $_rc (expected 2)"
fi
rm -rf "$_t7"

echo ""
echo "=== $_pass passed, $_fail failed ==="
[ $_fail -eq 0 ]
