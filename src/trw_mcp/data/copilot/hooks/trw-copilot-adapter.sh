#!/bin/sh
# TRW Copilot hook adapter — installed by trw-mcp bootstrap alongside hooks.json.
#
# Usage: trw-copilot-adapter.sh <hook_path> <event_name>
#   <hook_path>   Absolute path to the shared TRW .claude/hooks/<script>.sh
#   <event_name>  Copilot event name (preToolUse, postToolUse, sessionStart, …)
#
# Protocol:
#   1. Read the Copilot JSON payload from stdin.
#   2. Extract the "toolName" field (jq preferred, grep/sed fallback).
#      Export it as $TOOL_NAME so downstream TRW hooks can consume it.
#   3. Pipe the raw JSON payload to the target TRW hook script.
#   4. For preToolUse: translate the hook exit code into a JSON
#      permissionDecision object on stdout (exit 2 → deny, else → allow).
#   5. Fail-open: any error produces "allow" and exits 0 so the user's
#      tool call is never blocked by adapter infrastructure failures.
#
# This script is POSIX sh with no external dependencies beyond grep/sed
# (jq is used when available for reliability).  It intentionally contains
# NO nested shell quoting so it can be embedded as a plain argument to
# /bin/sh without escaping issues.

set -e
trap 'exit 0' EXIT

_hook_path="${1:-}"
_event_name="${2:-}"

# --- Read stdin payload ---
_input=$(cat) || true

# --- Extract toolName (jq preferred, grep/sed fallback) ---
TOOL_NAME=""
if command -v jq >/dev/null 2>&1; then
    TOOL_NAME=$(printf '%s' "$_input" | jq -r '.toolName // empty' 2>/dev/null) || true
fi
if [ -z "$TOOL_NAME" ]; then
    # POSIX grep/sed fallback — avoids any nested quoting in the outer command
    TOOL_NAME=$(printf '%s' "$_input" \
        | grep -o '"toolName"[[:space:]]*:[[:space:]]*"[^"]*"' 2>/dev/null \
        | head -1 \
        | sed 's/.*"toolName"[[:space:]]*:[[:space:]]*"//;s/"$//' 2>/dev/null) || true
fi
export TOOL_NAME

# --- Run target TRW hook ---
if [ -z "$_hook_path" ] || [ ! -f "$_hook_path" ]; then
    # Hook not installed yet — fail-open
    if [ "$_event_name" = "preToolUse" ]; then
        printf '{"permissionDecision":"allow"}'
    fi
    exit 0
fi

if [ "$_event_name" = "preToolUse" ]; then
    # preToolUse must emit a JSON permissionDecision on stdout.
    # Run the hook in a subshell so we can capture the exit code without
    # set -e terminating the script when the hook exits 2 (deny).
    _rc=0
    printf '%s' "$_input" | /bin/sh "$_hook_path" 2>/dev/null || _rc=$?
    if [ "$_rc" -eq 2 ]; then
        printf '{"permissionDecision":"deny"}'
    else
        printf '{"permissionDecision":"allow"}'
    fi
else
    # Non-permission hooks: pipe stdin to hook, fail-open on error.
    printf '%s' "$_input" | /bin/sh "$_hook_path" 2>/dev/null || true
fi

exit 0
