#!/usr/bin/env bash
# TRW Cursor hook — sessionStart
# Logs session start and injects TRW ceremony reminder into context.
set -euo pipefail

_LOG_DIR="${CURSOR_PROJECT_DIR:-${PWD}}/.trw/logs"
_LOG_FILE="${_LOG_DIR}/cursor-hooks.jsonl"

_log() {
  mkdir -p "${_LOG_DIR}" 2>/dev/null || true
  printf '{"ts":"%s","level":"info","component":"cursor-hook","event":"sessionStart","msg":%s}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$(printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null || echo '"<log-error>"')" \
    >> "${_LOG_FILE}" 2>/dev/null || true
}

# Consume stdin (may be empty or JSON event payload)
_INPUT="$(cat)"
_log "TRW session starting — project=${CURSOR_PROJECT_DIR:-unknown}"

printf '{"additional_context":"TRW: Call trw_session_start() as your first action to load prior learnings and active run state. Without it you start from zero."}\n'
