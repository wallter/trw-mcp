#!/usr/bin/env bash
# TRW Cursor hook — afterFileEdit
# Observer: logs file modification. No output injection.
set -euo pipefail

_LOG_DIR="${CURSOR_PROJECT_DIR:-${PWD}}/.trw/logs"
_LOG_FILE="${_LOG_DIR}/cursor-hooks.jsonl"

_log() {
  mkdir -p "${_LOG_DIR}" 2>/dev/null || true
  printf '{"ts":"%s","level":"info","component":"cursor-hook","event":"afterFileEdit","msg":%s}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$(printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null || echo '"<log-error>"')" \
    >> "${_LOG_FILE}" 2>/dev/null || true
}

_INPUT="$(cat)"
_FILE="$(printf '%s' "${_INPUT}" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("file_path","unknown"))' 2>/dev/null || echo "unknown")"
_log "afterFileEdit file=${_FILE}"

printf '{}\n'
