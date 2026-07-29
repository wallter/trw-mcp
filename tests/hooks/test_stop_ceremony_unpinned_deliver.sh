#!/usr/bin/env bash
# Shell-level tests for the stop-ceremony hook's UNPINNED-deliver handling and
# FOREIGN-run hardening.
#
# Regression target: an unpinned session that successfully ran trw_deliver was
# nagged ("trw_deliver() has not been called yet") because its completion marker
# lands only in .trw/context/session-events.jsonl (no run dir), while the hook
# attributed a parallel instance's newest run to it. The fix writes a
# recency-bounded session-scoped marker on every deliver and teaches the hook to
# trust it — and to not block an unpinned session on a foreign run's events.
set -u

_fail=0
_pass=0

_here="$(cd "$(dirname "$0")" && pwd)"
_hook="$_here/../../src/trw_mcp/data/hooks/stop-ceremony.sh"

if [ ! -r "$_hook" ]; then
  echo "FAIL: cannot find hook at $_hook"
  exit 1
fi

# A FOREIGN run with logged events and NO deliver marker — the state that
# (mis)drives a block when attributed to an unpinned session.
_add_foreign_run() {
  _run="$1/.trw/runs/foreigntask/20260101T000000Z-f0f0f0f0"
  mkdir -p "$_run/meta"
  printf 'task: foreigntask\n' >"$_run/meta/run.yaml"
  printf '{"ts":"2026-01-01T00:00:00Z","event":"file_modified"}\n' >"$_run/meta/events.jsonl"
}

_add_session_deliver() {
  mkdir -p "$1/.trw/context"
  printf '{"ts":"2026-01-01T00:00:00Z","event":"trw_deliver_complete","session_id":"%s"}\n' "$2" \
    >"$1/.trw/context/session-events.jsonl"
}

_check() {
  # $1=description $2=expected_rc $3=actual_rc
  if [ "$3" -eq "$2" ]; then
    _pass=$((_pass + 1)); echo "PASS: $1 (exit $3)"
  else
    _fail=$((_fail + 1)); echo "FAIL: $1 exited $3 (expected $2)"
  fi
}

# --- Test (a): unpinned session + recent session-events deliver -> exit 0 -----
_ta=$(mktemp -d)
mkdir -p "$_ta/.trw/context"
_add_session_deliver "$_ta" "sess-a"
CLAUDE_PROJECT_DIR="$_ta" sh "$_hook" <<EOF >/dev/null 2>&1
{"session_id":"sess-a","hook_event_name":"Stop"}
EOF
_check "unpinned session with recent session-events deliver is not nagged" 0 $?
rm -rf "$_ta"

# --- Test (b): FOREIGN run with events + NO deliver-for-this-session
#               + a recent session-events deliver -> still exit 0 -------------
_tb=$(mktemp -d)
_add_foreign_run "$_tb"
_add_session_deliver "$_tb" "sess-b"
CLAUDE_PROJECT_DIR="$_tb" sh "$_hook" <<EOF >/dev/null 2>&1
{"session_id":"sess-b","hook_event_name":"Stop"}
EOF
_check "foreign run events do not nag when a recent session deliver exists" 0 $?
rm -rf "$_tb"

# --- Test (b2): unpinned session (known id), foreign run events, NO deliver
#               -> not blocked on the foreign run's events (foreign hardening) --
_tb2=$(mktemp -d)
_add_foreign_run "$_tb2"
mkdir -p "$_tb2/.trw/context"
CLAUDE_PROJECT_DIR="$_tb2" sh "$_hook" <<EOF >/dev/null 2>&1
{"session_id":"unpinned-x","hook_event_name":"Stop"}
EOF
_check "unpinned identity is not nagged on a foreign run's events" 0 $?
rm -rf "$_tb2"

# --- Test (c): genuinely-not-delivered PINNED session -> blocks (exit 2) up to
#               the 2-block cap, then warns-and-allows (exit 0). --------------
_tc=$(mktemp -d)
_run="$_tc/.trw/runs/mytask/20260202T000000Z-aaaa1111"
mkdir -p "$_run/meta" "$_tc/.trw/runtime" "$_tc/.trw/context"
printf 'task: mytask\n' >"$_run/meta/run.yaml"
printf '{"ts":"2026-02-02T00:00:00Z","event":"file_modified"}\n' >"$_run/meta/events.jsonl"
# Pin this session to its OWN run.
printf '{"sess-c":{"run_path":"%s"}}\n' "$_run" >"$_tc/.trw/runtime/pins.json"

CLAUDE_PROJECT_DIR="$_tc" sh "$_hook" <<EOF >/dev/null 2>&1
{"session_id":"sess-c","hook_event_name":"Stop"}
EOF
_check "pinned not-delivered session blocks (reminder 1/2)" 2 $?

CLAUDE_PROJECT_DIR="$_tc" sh "$_hook" <<EOF >/dev/null 2>&1
{"session_id":"sess-c","hook_event_name":"Stop"}
EOF
_check "pinned not-delivered session blocks (reminder 2/2)" 2 $?

CLAUDE_PROJECT_DIR="$_tc" sh "$_hook" <<EOF >/dev/null 2>&1
{"session_id":"sess-c","hook_event_name":"Stop"}
EOF
_check "pinned not-delivered session warns-and-allows after cap" 0 $?
rm -rf "$_tc"

# --- Test (c2): a PINNED session whose own run DID deliver -> exit 0 ----------
_tc2=$(mktemp -d)
_run2="$_tc2/.trw/runs/donetask/20260303T000000Z-bbbb2222"
mkdir -p "$_run2/meta" "$_tc2/.trw/runtime" "$_tc2/.trw/context"
printf 'task: donetask\n' >"$_run2/meta/run.yaml"
printf '{"ts":"2026-03-03T00:00:00Z","event":"file_modified"}\n{"ts":"2026-03-03T00:01:00Z","event":"trw_deliver_complete","session_id":"sess-d"}\n' \
  >"$_run2/meta/events.jsonl"
printf '{"sess-d":{"run_path":"%s"}}\n' "$_run2" >"$_tc2/.trw/runtime/pins.json"
CLAUDE_PROJECT_DIR="$_tc2" sh "$_hook" <<EOF >/dev/null 2>&1
{"session_id":"sess-d","hook_event_name":"Stop"}
EOF
_check "pinned delivered session is not nagged" 0 $?
rm -rf "$_tc2"

# --- Test (d): a STALE session-events deliver (mtime older than the window)
#               must NOT clear a pinned not-delivered session's block. --------
_td=$(mktemp -d)
_run3="$_td/.trw/runs/staletask/20260404T000000Z-cccc3333"
mkdir -p "$_run3/meta" "$_td/.trw/runtime" "$_td/.trw/context"
printf 'task: staletask\n' >"$_run3/meta/run.yaml"
printf '{"ts":"2026-04-04T00:00:00Z","event":"file_modified"}\n' >"$_run3/meta/events.jsonl"
printf '{"sess-e":{"run_path":"%s"}}\n' "$_run3" >"$_td/.trw/runtime/pins.json"
_se="$_td/.trw/context/session-events.jsonl"
printf '{"ts":"2020-01-01T00:00:00Z","event":"trw_deliver_complete","session_id":"other"}\n' >"$_se"
# Age the marker file well beyond the 240-minute recency window.
touch -d "1 year ago" "$_se" 2>/dev/null || touch -t 202001010000 "$_se" 2>/dev/null || true
CLAUDE_PROJECT_DIR="$_td" sh "$_hook" <<EOF >/dev/null 2>&1
{"session_id":"sess-e","hook_event_name":"Stop"}
EOF
_check "stale session-events deliver does not clear a pinned block" 2 $?
rm -rf "$_td"

echo ""
echo "=== $_pass passed, $_fail failed ==="
[ $_fail -eq 0 ]
