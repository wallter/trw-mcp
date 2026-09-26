#!/usr/bin/env bash
# TRW Cursor hook — beforeSubmitPrompt
# Observer: logs prompt submission, emits continue=true.
set -euo pipefail

_LOG_DIR="${CURSOR_PROJECT_DIR:-${PWD}}/.trw/logs"
_LOG_FILE="${_LOG_DIR}/cursor-hooks.jsonl"
# PRD-SEC/RC8: refuse to log through a symlinked .trw, .trw/logs, or log
# file -- a crafted checkout must not be able to redirect this append at an
# arbitrary target the user can write. Logging is best-effort (never blocks
# the gate this hook may also be deciding), so the fix is to silently drop
# the log line, not to abort.
if [ -L "${_LOG_DIR%/logs}" ] || [ -L "$_LOG_DIR" ] || [ -L "$_LOG_FILE" ]; then
    _LOG_FILE="/dev/null"
fi

_log() {
  mkdir -p "${_LOG_DIR}" 2>/dev/null || true
  printf '{"ts":"%s","level":"info","component":"cursor-hook","event":"beforeSubmitPrompt","msg":%s}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$(printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null || echo '"<log-error>"')" \
    >> "${_LOG_FILE}" 2>/dev/null || true
}

_INPUT="$(cat)"
_log "beforeSubmitPrompt"

printf '{"continue":true}\n'
