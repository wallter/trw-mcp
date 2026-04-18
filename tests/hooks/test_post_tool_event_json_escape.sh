#!/usr/bin/env bash
# Security audit 2026-04-18 H1: shell-level test that post-tool-event.sh
# escapes attacker-controlled file_path / tool_name values before writing
# to events.jsonl. A payload containing unescaped `"` or `\` previously
# split the JSONL line.
set -u

_fail=0
_pass=0

_here="$(cd "$(dirname "$0")" && pwd)"
_hook="$_here/../../src/trw_mcp/data/hooks/post-tool-event.sh"
_lib="$_here/../../src/trw_mcp/data/hooks/lib-trw.sh"

if [ ! -r "$_hook" ] || [ ! -r "$_lib" ]; then
  echo "FAIL: cannot find hook or lib at $_hook / $_lib"
  exit 1
fi

# ── Test 1: _json_escape fundamentals (unit test against lib-trw.sh) ──

_escape() {
  # Source lib-trw.sh in a subshell and call _json_escape.
  ( . "$_lib" 2>/dev/null; _json_escape "$1" )
}

_got=$(_escape 'plain')
if [ "$_got" = "plain" ]; then
  _pass=$((_pass + 1)); echo "PASS: plain text unchanged"
else
  _fail=$((_fail + 1)); echo "FAIL: plain -> $_got"
fi

_got=$(_escape 'quote"here')
if [ "$_got" = 'quote\"here' ]; then
  _pass=$((_pass + 1)); echo "PASS: double-quote escaped"
else
  _fail=$((_fail + 1)); echo "FAIL: quote -> $_got"
fi

_got=$(_escape 'back\slash')
if [ "$_got" = 'back\\slash' ]; then
  _pass=$((_pass + 1)); echo "PASS: backslash escaped"
else
  _fail=$((_fail + 1)); echo "FAIL: backslash -> $_got"
fi

# ── Test 2: end-to-end — craft an active run and fire the hook with
#          a malicious file_path, then assert events.jsonl is valid JSONL
#          and contains exactly one event line with the escaped payload.

_setup_project() {
  _p=$(mktemp -d)
  _run="$_p/.trw/runs/demo/20260418T000000Z-test"
  mkdir -p "$_run/meta"
  # Minimal run.yaml so find_active_run picks this run up.
  printf 'task: demo\n' > "$_run/meta/run.yaml"
  printf '%s' "$_p"
}

_project=$(_setup_project)
_events="$_project/.trw/runs/demo/20260418T000000Z-test/meta/events.jsonl"

# Payload with a file_path that would split JSONL if not escaped.
_malicious='{"tool_name":"Write","tool_input":{"file_path":"evil\",\"event\":\"rce_injected\",\"x\":\"y.py"}}'

printf '%s' "$_malicious" | CLAUDE_PROJECT_DIR="$_project" sh "$_hook" >/dev/null 2>&1 || true

if [ ! -f "$_events" ]; then
  _fail=$((_fail + 1)); echo "FAIL: events.jsonl not written at $_events"
else
  _line_count=$(wc -l < "$_events" | tr -d ' ')
  if [ "$_line_count" = "1" ]; then
    _pass=$((_pass + 1)); echo "PASS: exactly one JSONL line written (attack didn't split)"
  else
    _fail=$((_fail + 1)); echo "FAIL: $_line_count lines written — JSONL was split"
    cat "$_events"
  fi

  # Assert the synthetic event name from the payload is NOT an emitted event
  if grep -q '"event":"rce_injected"' "$_events"; then
    _fail=$((_fail + 1)); echo "FAIL: injected event name appeared verbatim"
  else
    _pass=$((_pass + 1)); echo "PASS: no injected event name in output"
  fi

  # jq round-trip: if jq is available, the line must be valid JSON
  if command -v jq >/dev/null 2>&1; then
    if jq . < "$_events" >/dev/null 2>&1; then
      _pass=$((_pass + 1)); echo "PASS: jq parses events.jsonl without error"
    else
      _fail=$((_fail + 1)); echo "FAIL: jq failed to parse events.jsonl"
    fi
  fi
fi

rm -rf "$_project"

echo "---"
echo "Passed: $_pass"
echo "Failed: $_fail"
[ "$_fail" -eq 0 ]
