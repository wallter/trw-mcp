#!/usr/bin/env bash
# TRW afterMCPExecution hook — observer only; logs best-effort; emits {} on stdout.
# failClosed: false — crash or timeout is safe (observer, not a gate).
set -euo pipefail

PAYLOAD="$(cat)"

# Best-effort structured log to .trw/logs/cursor-hooks.jsonl
LOG_DIR="${CURSOR_PROJECT_DIR:-$(pwd)}/.trw/logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
TS="$(date -Iseconds 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)"
EVENT_NAME="afterMCPExecution"

# Extract tool name if jq is available
if command -v jq >/dev/null 2>&1; then
    TOOL="$(echo "$PAYLOAD" | jq -r '.tool_name // "unknown"' 2>/dev/null || echo "unknown")"
else
    TOOL="$(echo "$PAYLOAD" | grep -oP '"tool_name"\s*:\s*"\K[^"]+' 2>/dev/null || echo "unknown")"
fi

echo "{\"ts\":\"$TS\",\"event\":\"$EVENT_NAME\",\"tool\":\"$TOOL\"}" >> "$LOG_DIR/cursor-hooks.jsonl" 2>/dev/null || true

# Observer: emit empty JSON object (Cursor ignores content for observer hooks)
echo '{}'
