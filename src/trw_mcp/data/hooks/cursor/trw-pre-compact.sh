#!/usr/bin/env bash
# TRW Cursor hook — preCompact
#
# Context compaction is a genuinely critical moment (in-flight state risks
# being dropped). Gate is short (5 min per generation_id) — each distinct
# compaction event triggers at most one reminder, but the same generation
# firing twice within 5 minutes is deduplicated.
set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LOG_DIR="${CURSOR_PROJECT_DIR:-${PWD}}/.trw/logs"
_LOG_FILE="${_LOG_DIR}/cursor-hooks.jsonl"
mkdir -p "${_LOG_DIR}" 2>/dev/null || true

_INPUT_TMP="$(mktemp)"
trap 'rm -f "${_INPUT_TMP}"' EXIT
cat > "${_INPUT_TMP}"

{
  printf '{"ts":"%s","level":"warn","component":"cursor-hook","event":"preCompact","msg":"context window compaction imminent"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >> "${_LOG_FILE}" 2>/dev/null || true

python3 "${_SCRIPT_DIR}/_nudge_gate.py" \
  preCompact \
  300 \
  trw_pre_compact_checkpoint \
  user_message \
  '[
    "TRW: Context compaction imminent. Call trw_pre_compact_checkpoint() now — it preserves your resumption point across the compression boundary."
  ]' \
  < "${_INPUT_TMP}"
