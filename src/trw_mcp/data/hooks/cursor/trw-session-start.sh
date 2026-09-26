#!/usr/bin/env bash
# TRW Cursor hook — sessionStart
#
# Emits a session-start reminder, gated by the shared nudge gate:
#   * cooldown: once per conversation_id, per 24 hours
#   * adaptive skip: suppress if trw_session_start has already fired recently
#   * rotation: stable per-conversation selection from a curated set
set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
mkdir -p "${_LOG_DIR}" 2>/dev/null || true

_INPUT_TMP="$(mktemp)"
trap 'rm -f "${_INPUT_TMP}"' EXIT
cat > "${_INPUT_TMP}"

{
  printf '{"ts":"%s","level":"info","component":"cursor-hook","event":"sessionStart","msg":"session starting"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >> "${_LOG_FILE}" 2>/dev/null || true

python3 "${_SCRIPT_DIR}/_nudge_gate.py" \
  sessionStart \
  86400 \
  trw_session_start \
  additional_context \
  '[
    "TRW: Call trw_session_start() as your first action to load prior learnings and any active run state.",
    "TRW: Begin with trw_session_start() — prior learnings compound; without it you start from zero.",
    "TRW: trw_session_start() loads the team'"'"'s accumulated engineering memory before you write code."
  ]' \
  < "${_INPUT_TMP}"
