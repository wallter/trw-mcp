#!/usr/bin/env bash
# TRW Cursor hook — stop
#
# Emits conditional preservation advice, gated by the shared nudge gate:
#   * anti-fatigue cooldown: once per conversation_id, per hour
#   * adaptive skip: suppress if trw_deliver has already been invoked recently
#   * message rotation: stable per-conversation selection from a curated set
#
# Default output: empty JSON (observer-only) when the gate suppresses.
# Previous behavior (unconditional followup_message on every fire) caused the
# same notification to be displayed 4+ times per session in cursor-ide.
set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LOG_DIR="${CURSOR_PROJECT_DIR:-${PWD}}/.trw/logs"
_LOG_FILE="${_LOG_DIR}/cursor-hooks.jsonl"
mkdir -p "${_LOG_DIR}" 2>/dev/null || true

# Read stdin once and tee to two consumers: the observability log AND the
# nudge gate. mktemp avoids holding the entire payload in a bash variable.
_INPUT_TMP="$(mktemp)"
trap 'rm -f "${_INPUT_TMP}"' EXIT
cat > "${_INPUT_TMP}"

# Observability (fire-and-forget, never blocks hook response)
{
  printf '{"ts":"%s","level":"info","component":"cursor-hook","event":"stop","msg":"stop event received"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >> "${_LOG_FILE}" 2>/dev/null || true

# Invoke the gate. Args: event cooldown adaptive_skip_tool response_key messages_json
python3 "${_SCRIPT_DIR}/_nudge_gate.py" \
  stop \
  3600 \
  trw_deliver \
  followup_message \
  '[
    "TRW: If you have material unfinished work, use trw_checkpoint or a durable native handoff with a next-read pointer. With nothing material, do not invent an artifact. Use trw_deliver for completed work under existing gates.",
    "TRW: If you have material unfinished work, preserve progress, checks, risks and next action via trw_checkpoint or a durable native handoff with a next-read pointer. With nothing material, do not invent an artifact. Acceptance of completed work uses trw_deliver under existing gates.",
    "TRW: If you have material unfinished work, preserve it via trw_checkpoint or a durable native handoff and leave a next-read pointer; preservation is not completion. With nothing material, do not invent an artifact. For completed work, trw_deliver remains subject to existing gates."
  ]' \
  < "${_INPUT_TMP}"
