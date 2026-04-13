#!/usr/bin/env bash
# TRW Cursor hooks CLI adapter — shared JSON dispatcher
# Reads JSON stdin, extracts hook_event_name, routes to event-specific handler.
# Part of TRW Framework (https://trwframework.com)
set -euo pipefail

# ---------------------------------------------------------------------------
# Logging (best-effort — errors are swallowed so the hook never blocks Cursor)
# ---------------------------------------------------------------------------

_LOG_DIR="${CURSOR_PROJECT_DIR:-${PWD}}/.trw/logs"
_LOG_FILE="${_LOG_DIR}/cursor-hooks.jsonl"

_log() {
  local level="$1" msg="$2"
  mkdir -p "${_LOG_DIR}" 2>/dev/null || true
  printf '{"ts":"%s","level":"%s","component":"cursor-hook","msg":%s}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "${level}" \
    "$(printf '%s' "${msg}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null || echo '"<log-error>"')" \
    >> "${_LOG_FILE}" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Read stdin once into a variable
# ---------------------------------------------------------------------------

INPUT="$(cat)"

# ---------------------------------------------------------------------------
# Extract hook_event_name from JSON input
# ---------------------------------------------------------------------------

_event_name() {
  printf '%s' "${INPUT}" | python3 -c \
    'import json,sys; d=json.load(sys.stdin); print(d.get("hook_event_name","unknown"))' \
    2>/dev/null || echo "unknown"
}

EVENT="$(_event_name)"
_log "info" "cli-adapter dispatching event=${EVENT}"

# ---------------------------------------------------------------------------
# Route to event handler (sourcing per-event scripts if present)
# ---------------------------------------------------------------------------

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${EVENT}" in
  sessionStart)
    exec "${HOOK_DIR}/trw-session-start.sh" <<< "${INPUT}" ;;
  beforeMCPExecution)
    exec "${HOOK_DIR}/trw-before-mcp.sh" <<< "${INPUT}" ;;
  postToolUse)
    exec "${HOOK_DIR}/trw-post-tool-use.sh" <<< "${INPUT}" ;;
  preToolUse)
    exec "${HOOK_DIR}/trw-pre-tool-use.sh" <<< "${INPUT}" ;;
  afterFileEdit)
    exec "${HOOK_DIR}/trw-after-file-edit.sh" <<< "${INPUT}" ;;
  preCompact)
    exec "${HOOK_DIR}/trw-pre-compact.sh" <<< "${INPUT}" ;;
  stop)
    exec "${HOOK_DIR}/trw-stop.sh" <<< "${INPUT}" ;;
  beforeSubmitPrompt)
    exec "${HOOK_DIR}/trw-before-submit-prompt.sh" <<< "${INPUT}" ;;
  *)
    _log "warn" "cli-adapter: unhandled event ${EVENT}"
    printf '{}\n'
    ;;
esac
