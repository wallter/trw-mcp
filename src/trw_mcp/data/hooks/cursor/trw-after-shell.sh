#!/usr/bin/env bash
# TRW afterShellExecution hook — observer only; logs command + duration.
# failClosed: false — crash or timeout is safe (observer, not a gate).
set -euo pipefail

PAYLOAD="$(cat)"

# Best-effort structured log
LOG_DIR="${CURSOR_PROJECT_DIR:-$(pwd)}/.trw/logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
TS="$(date -Iseconds 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)"

# Extract command and duration from payload
if command -v jq >/dev/null 2>&1; then
    CMD="$(echo "$PAYLOAD" | jq -r '.command // "unknown"' 2>/dev/null || echo "unknown")"
    DURATION="$(echo "$PAYLOAD" | jq -r '.duration_ms // "unknown"' 2>/dev/null || echo "unknown")"
    EXIT_CODE="$(echo "$PAYLOAD" | jq -r '.exit_code // "unknown"' 2>/dev/null || echo "unknown")"
else
    CMD="$(echo "$PAYLOAD" | grep -oP '"command"\s*:\s*"\K[^"]+' 2>/dev/null || echo "unknown")"
    DURATION="unknown"
    EXIT_CODE="unknown"
fi

# Truncate command for log readability
CMD_PREFIX="${CMD:0:120}"

echo "{\"ts\":\"$TS\",\"event\":\"afterShellExecution\",\"command_prefix\":\"$CMD_PREFIX\",\"duration_ms\":\"$DURATION\",\"exit_code\":\"$EXIT_CODE\"}" >> "$LOG_DIR/cursor-hooks.jsonl" 2>/dev/null || true

# Observer: emit empty JSON object
echo '{}'
