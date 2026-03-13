#!/usr/bin/env bash
# lib-ide-adapter.sh — IDE input format abstraction for shared hook scripts
# PRD-CORE-074-FR10: Shared Hook Script Library
#
# Source this from IDE-specific hook wrappers to normalize input formats.
#
# Claude Code sends JSON on stdin with specific fields.
# Cursor sends JSON on stdin with different field names.
# This adapter normalizes both into a common format.
#
# Usage: source "$(dirname "${BASH_SOURCE[0]}")/lib-ide-adapter.sh"
#
# The file must remain sourceable — no exit() at top level.
# Fail-open: sourcing side-effects must not abort the calling script.

set -euo pipefail

# detect_ide_caller: Detect which IDE is calling this hook.
# Prints one of: claude-code, cursor, unknown
detect_ide_caller() {
    # Claude Code sets CLAUDE_CODE_ENTRYPOINT or CLAUDE_CODE env vars
    if [[ -n "${CLAUDE_CODE_ENTRYPOINT:-}" ]] || [[ -n "${CLAUDE_CODE:-}" ]]; then
        printf "claude-code"
    elif [[ -n "${CURSOR_SESSION:-}" ]] || [[ -n "${CURSOR_IDE:-}" ]]; then
        printf "cursor"
    else
        # Fallback: unknown IDE
        printf "unknown"
    fi
}

# extract_json_field: Extract a top-level string field from JSON without jq.
# Usage: extract_json_field "field_name" <<< "$json_input"
# Returns the field value, or empty string if not found.
# Portable: uses grep + sed only (no jq, no grep -P).
extract_json_field() {
    local field="$1"
    # Match: "field_name"  :  "value"  (handles optional whitespace)
    grep -o "\"${field}\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" 2>/dev/null \
        | head -1 \
        | sed "s/\"${field}\"[[:space:]]*:[[:space:]]*\"//;s/\"$//" \
        || printf ""
}

# normalize_hook_input: Normalize hook event data from stdin.
# Reads JSON from stdin, prints key=value pairs on stdout.
# Callers should eval or parse the output lines as needed.
#
# Output variables:
#   IDE=<claude-code|cursor|unknown>
#   TOOL_NAME=<normalized tool/hook name>
#   EVENT=<event field value, if present>
normalize_hook_input() {
    local raw_input
    raw_input="$(cat)"

    local ide
    ide="$(detect_ide_caller)"

    case "$ide" in
        claude-code)
            # Claude Code sends: {"tool_name": "...", "tool_input": {...}, "event": "..."}
            printf "IDE=claude-code\n"
            printf "TOOL_NAME=%s\n" "$(printf '%s' "$raw_input" | extract_json_field "tool_name")"
            printf "EVENT=%s\n" "$(printf '%s' "$raw_input" | extract_json_field "event")"
            ;;
        cursor)
            # Cursor sends: {"hookName": "...", "params": {...}, "event": "..."}
            printf "IDE=cursor\n"
            printf "TOOL_NAME=%s\n" "$(printf '%s' "$raw_input" | extract_json_field "hookName")"
            printf "EVENT=%s\n" "$(printf '%s' "$raw_input" | extract_json_field "event")"
            ;;
        *)
            printf "IDE=unknown\n"
            ;;
    esac
}

# check_enforcement_variant: Return 0 if hooks should run, 1 if disabled.
# Sources lib-trw.sh for trw_should_run_hooks().
# Fail-open: if lib-trw.sh is unavailable, hooks are allowed to run (return 0).
check_enforcement_variant() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    # Source lib-trw.sh if available
    if [[ -f "${script_dir}/lib-trw.sh" ]]; then
        # shellcheck source=lib-trw.sh
        source "${script_dir}/lib-trw.sh"
        if ! trw_should_run_hooks 2>/dev/null; then
            return 1
        fi
    fi
    return 0
}
