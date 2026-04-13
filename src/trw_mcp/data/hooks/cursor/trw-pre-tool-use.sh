#!/usr/bin/env bash
# TRW Cursor hook — preToolUse
# Observer: logs tool invocation, emits allow permission.
set -euo pipefail

_LOG_DIR="${CURSOR_PROJECT_DIR:-${PWD}}/.trw/logs"
_LOG_FILE="${_LOG_DIR}/cursor-hooks.jsonl"

_log() {
  mkdir -p "${_LOG_DIR}" 2>/dev/null || true
  printf '{"ts":"%s","level":"info","component":"cursor-hook","event":"preToolUse","msg":%s}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$(printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null || echo '"<log-error>"')" \
    >> "${_LOG_FILE}" 2>/dev/null || true
}

_INPUT="$(cat)"
_TOOL="$(printf '%s' "${_INPUT}" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("tool_name","unknown"))' 2>/dev/null || echo "unknown")"
_log "preToolUse tool=${_TOOL}"

printf '{"permission":"allow"}\n'
