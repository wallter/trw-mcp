#!/usr/bin/env bash
# TRW beforeShellExecution hook — security-critical gate.
# failClosed: true — a crash or timeout causes the shell command to be denied.
# Checks for common secret-leak patterns in the command string.
# Emits {"permission":"deny","user_message":"..."} if a secret pattern is
# detected; otherwise emits {"permission":"allow"}.
set -euo pipefail

PAYLOAD="$(cat)"

# Best-effort structured log
LOG_DIR="${CURSOR_PROJECT_DIR:-$(pwd)}/.trw/logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
TS="$(date -Iseconds 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)"

# Extract command string from payload
if command -v jq >/dev/null 2>&1; then
    CMD="$(echo "$PAYLOAD" | jq -r '.command // ""' 2>/dev/null || echo "")"
else
    CMD="$(echo "$PAYLOAD" | grep -oP '"command"\s*:\s*"\K[^"]+' 2>/dev/null || echo "")"
fi

echo "{\"ts\":\"$TS\",\"event\":\"beforeShellExecution\",\"command_prefix\":\"${CMD:0:80}\"}" >> "$LOG_DIR/cursor-hooks.jsonl" 2>/dev/null || true

# Secret-leak pattern detection — matches assignment-style secrets in command strings
# Patterns: password=VALUE, API_KEY=VALUE, secret=VALUE, token=VALUE
# A "value" must be at least one non-whitespace character.
if echo "$CMD" | grep -qiE '(password|api_key|secret|token)=[^[:space:]]+'; then
    REASON="possible secret leak detected in shell command"
    echo "{\"ts\":\"$TS\",\"event\":\"beforeShellExecution\",\"action\":\"deny\",\"reason\":\"$REASON\"}" >> "$LOG_DIR/cursor-hooks.jsonl" 2>/dev/null || true
    printf '{"permission":"deny","user_message":"TRW: %s"}\n' "$REASON"
    exit 0
fi

echo '{"permission":"allow"}'
