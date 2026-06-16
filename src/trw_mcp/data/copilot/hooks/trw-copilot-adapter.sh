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
# preToolUse chaining (PRD-DIST-2459 FR-5):
#   The single preToolUse slot runs BOTH the ceremony deliver-gate (the
#   <hook_path> argument) AND the trw-distill advisory hint hook
#   (trw-copilot-distill-hint.sh, located next to this adapter), SEQUENTIALLY.
#     - The deliver-gate's decision is AUTHORITATIVE. If it blocks (exit 2)
#       the adapter emits {"permissionDecision":"deny"} and the distill-hint
#       is NEVER run — a block always wins.
#     - Only when the deliver-gate ALLOWS does the adapter run the distill-hint.
#       The hint is advisory: its plain-text stdout (if any) rides in the
#       permissionDecisionReason of an ALLOW. The hint can NEVER convert an
#       allow into a deny and NEVER blocks (it always exits 0).
#   This preserves the deliver-gate's existing block/allow behavior byte-for-
#   byte; the distill-hint is purely additive and opt-in (cc03_hook_enabled).
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
    # Run the deliver-gate in a subshell so we can capture the exit code without
    # set -e terminating the script when the hook exits 2 (deny).
    _rc=0
    printf '%s' "$_input" | /bin/sh "$_hook_path" 2>/dev/null || _rc=$?
    if [ "$_rc" -eq 2 ]; then
        # AUTHORITATIVE BLOCK: deliver-gate denied — the distill-hint is never
        # consulted. A block always wins (PRD-DIST-2459 FR-5).
        printf '{"permissionDecision":"deny"}'
        exit 0
    fi

    # Deliver-gate ALLOWED. Run the advisory trw-distill hint (chained,
    # non-blocking). Its plain-text stdout (if any) rides as the
    # permissionDecisionReason of an ALLOW. The hint can NEVER deny.
    _adapter_dir=$(dirname "$0")
    _hint_script="$_adapter_dir/trw-copilot-distill-hint.sh"
    _hint=""
    if [ -f "$_hint_script" ]; then
        # The hint hook always exits 0; `|| true` guards any unexpected non-zero.
        _hint=$(printf '%s' "$_input" | /bin/sh "$_hint_script" 2>/dev/null) || true
    fi

    if [ -n "$_hint" ]; then
        # JSON-escape the advisory text into permissionDecisionReason. POSIX
        # sed: escape backslash, double-quote, tab, CR; collapse newlines to \n.
        _reason=$(printf '%s' "$_hint" \
            | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' \
                  -e 's/	/\\t/g' \
            | awk 'BEGIN{ORS=""} {if(NR>1) printf "\\n"; printf "%s", $0}')
        printf '{"permissionDecision":"allow","permissionDecisionReason":"%s"}' "$_reason"
    else
        printf '{"permissionDecision":"allow"}'
    fi
    exit 0
else
    # Non-permission hooks: pipe stdin to hook, fail-open on error.
    printf '%s' "$_input" | /bin/sh "$_hook_path" 2>/dev/null || true
fi

exit 0
