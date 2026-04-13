#!/usr/bin/env bash
# TRW Cursor hook — preCompact
# Logs pre-compact event and reminds agent to checkpoint before compaction.
set -euo pipefail

_LOG_DIR="${CURSOR_PROJECT_DIR:-${PWD}}/.trw/logs"
_LOG_FILE="${_LOG_DIR}/cursor-hooks.jsonl"

_log() {
  mkdir -p "${_LOG_DIR}" 2>/dev/null || true
  printf '{"ts":"%s","level":"warn","component":"cursor-hook","event":"preCompact","msg":%s}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$(printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null || echo '"<log-error>"')" \
    >> "${_LOG_FILE}" 2>/dev/null || true
}

_INPUT="$(cat)"
_log "preCompact — context window compaction imminent"

printf '{"user_message":"TRW: Context compaction imminent. Call trw_pre_compact_checkpoint() now — it preserves your resumption point across the compression boundary (Cursor'"'"'s native compaction can drop in-flight state otherwise)."}\n'
