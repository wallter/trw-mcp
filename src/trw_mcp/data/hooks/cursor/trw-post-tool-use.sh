#!/usr/bin/env bash
# TRW Cursor hook — postToolUse
# Observer: logs tool completion; emits ceremony reminder after trw_deliver.
set -euo pipefail

_LOG_DIR="${CURSOR_PROJECT_DIR:-${PWD}}/.trw/logs"
_LOG_FILE="${_LOG_DIR}/cursor-hooks.jsonl"

_log() {
  mkdir -p "${_LOG_DIR}" 2>/dev/null || true
  printf '{"ts":"%s","level":"info","component":"cursor-hook","event":"postToolUse","msg":%s}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$(printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null || echo '"<log-error>"')" \
    >> "${_LOG_FILE}" 2>/dev/null || true
}

_INPUT="$(cat)"
_TOOL="$(printf '%s' "${_INPUT}" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("tool_name","unknown"))' 2>/dev/null || echo "unknown")"
_log "postToolUse tool=${_TOOL}"

# Emit a ceremony reminder after key TRW ceremony tools
case "${_TOOL}" in
  trw_learn|trw_checkpoint)
    printf '{"additional_context":"TRW ceremony reminder: learnings recorded. Call trw_deliver() at session end to persist."}\n'
    ;;
  *)
    printf '{}\n'
    ;;
esac
