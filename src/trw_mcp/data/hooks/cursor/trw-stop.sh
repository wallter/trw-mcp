#!/usr/bin/env bash
# TRW Cursor hook — stop
# Emits followup message reminding the agent to call trw_deliver().
set -euo pipefail

_LOG_DIR="${CURSOR_PROJECT_DIR:-${PWD}}/.trw/logs"
_LOG_FILE="${_LOG_DIR}/cursor-hooks.jsonl"

_log() {
  mkdir -p "${_LOG_DIR}" 2>/dev/null || true
  printf '{"ts":"%s","level":"info","component":"cursor-hook","event":"stop","msg":%s}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$(printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null || echo '"<log-error>"')" \
    >> "${_LOG_FILE}" 2>/dev/null || true
}

_INPUT="$(cat)"
_log "stop event received — session ending"

printf '{"followup_message":"TRW: session ending — call trw_deliver() to persist learnings and checkpoint progress. Without it, your session discoveries will not compound for future agents."}\n'
