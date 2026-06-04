#!/bin/sh
# CC-03 PreToolUse hook: trw-distill edit hint.
#
# PRD-DIST-2405 FR25-FR32.
#
# Reads PreToolUse JSON from stdin and emits an advisory hint on stdout.
# NEVER exits 2 under any condition (FR26) — always advisory, never blocking.
# Default state: opt-in (exits 0 silently unless cc03_hook_enabled: true).
#
# POSIX sh compatible (NFR08). Tested with: dash, sh, bash.
#
# Skip conditions (all exit 0 silently) — FR27:
#   1. cc03_hook_enabled: false (default opt-in gate)
#   2. file_path resolves outside repo root
#   3. agent_type in {trw-distill-explorer, Explore, Plan}
#   4. file extension in safe-skip allowlist
#   5. same file_path hinted within last 180 seconds (debounce)
#   6. Python import fails AND no learnings match
#
# Hook latency budget: ≤ 3000ms registered timeout (NFR06).
# Python subprocess: ≤ 2500ms (timeout 2.5); fallback to T0 beacon on timeout (FR30).

set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-distill-hint.sh
. "$_hook_dir/lib-distill-hint.sh" 2>/dev/null || exit 0

# --- Read JSON payload from stdin ---
_payload=$(cat 2>/dev/null) || exit 0

# Extract fields — jq preferred, grep fallback (FR25)
_tool_use_id=""
_file_path=""
_tool_name=""
_agent_type=""

if command -v jq >/dev/null 2>&1; then
    _tool_use_id=$(printf '%s' "$_payload" | jq -r '.tool_use_id // empty' 2>/dev/null) || true
    _file_path=$(printf '%s' "$_payload" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || true
    _tool_name=$(printf '%s' "$_payload" | jq -r '.tool_name // empty' 2>/dev/null) || true
    _agent_type=$(printf '%s' "$_payload" | jq -r '.agent_name // empty' 2>/dev/null) || true
else
    # grep/sed fallback (FR25)
    _tool_use_id=$(printf '%s' "$_payload" | grep -o '"tool_use_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"tool_use_id"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
    _file_path=$(printf '%s' "$_payload" | grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"file_path"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
    _tool_name=$(printf '%s' "$_payload" | grep -o '"tool_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"tool_name"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
    _agent_type=$(printf '%s' "$_payload" | grep -o '"agent_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"agent_name"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
fi

# --- Skip 1: opt-in gate (FR09) ---
if ! _get_cc03_enabled; then
    exit 0
fi

# --- Skip 2: no file_path ---
[ -n "$_file_path" ] || exit 0

# --- Skip 3: agent_type exclusion ---
case "$_agent_type" in
    trw-distill-explorer|Explore|Plan) exit 0 ;;
esac

# --- Skip 4: safe extension allowlist (P0-10 fix) ---
if _is_safe_extension "$_file_path"; then
    exit 0
fi

# --- Skip 5: debounce (same file within last 180s) ---
_repo="${TRW_PROJECT_DIR:-$(pwd)}"
_debounce_dir="${_repo}/.trw/context/cc03-debounce"
if [ -d "$_debounce_dir" ]; then
    _safe_name=$(printf '%s' "$_file_path" | tr '/' '_' | tr -cd 'a-zA-Z0-9_.-')
    _debounce_file="${_debounce_dir}/${_safe_name}.ts"
    if [ -f "$_debounce_file" ]; then
        _now=$(date +%s 2>/dev/null) || _now=0
        _last=$(cat "$_debounce_file" 2>/dev/null) || _last=0
        _diff=$(( _now - _last ))
        if [ "$_diff" -lt 180 ] 2>/dev/null; then
            exit 0
        fi
    fi
    mkdir -p "$_debounce_dir" 2>/dev/null || true
    date +%s > "$_debounce_file" 2>/dev/null || true
else
    mkdir -p "$_debounce_dir" 2>/dev/null || true
    _safe_name=$(printf '%s' "$_file_path" | tr '/' '_' | tr -cd 'a-zA-Z0-9_.-')
    date +%s > "${_debounce_dir}/${_safe_name}.ts" 2>/dev/null || true
fi

# --- Resolve Python path ---
_py=$(_get_python_path 2>/dev/null) || {
    _format_t0_beacon
    exit 0
}

# --- Call compute_before_edit_hint via Python subprocess (FR30) ---
# Timeout of 2500ms (timeout 2.5); fall back to T0 beacon on failure/timeout.
_hints_dir="${_repo}/.trw/context/cc03-hints"
mkdir -p "$_hints_dir" 2>/dev/null || true

_hint_output=$(
    PYTHONDONTWRITEBYTECODE=1 PYTHONOPTIMIZE=1 \
    timeout 2.5 "$_py" -c "
import sys, json
try:
    from trw_mcp.tools.before_edit_hint import compute_before_edit_hint
    from trw_mcp.channels.claude_code._hook_helpers import (
        format_t0_beacon, format_t1_hint, format_t2_hint
    )
    result = compute_before_edit_hint(file_path='${_file_path}')
    hint = result.distill_hint
    learnings = [{'summary': l.summary} for l in result.learnings]
    if hint and result.distill_status == 'hint_available':
        output = format_t2_hint(
            file_path='${_file_path}',
            risk_score=hint.risk_score,
            hotspot_warnings=hint.hotspot_warnings,
            co_change_neighbors=hint.co_change_neighbors,
            inferred_tests=hint.inferred_tests,
        )
        tier = 'T2'
    elif learnings:
        output = format_t1_hint(learnings)
        tier = 'T1'
    else:
        output = format_t0_beacon()
        tier = 'T0'
    # FR29: write hint file with tool_use_id
    if '${_tool_use_id}':
        from trw_mcp.channels.claude_code._hook_helpers import write_hint_file
        from pathlib import Path
        write_hint_file(
            hints_dir=Path('${_hints_dir}'),
            tool_use_id='${_tool_use_id}',
            file_path='${_file_path}',
            tier=tier,
            hint_emitted=True,
            tokens_emitted=len(output.split()),
            distill_status=result.distill_status,
        )
    print(output)
except Exception as e:
    print('[TRW] Distill intelligence available — run trw_before_edit_hint for details.')
" 2>/dev/null
) || {
    # Timeout or error: fall back to T0 beacon (FR30, FR31)
    _format_t0_beacon
    exit 0
}

# --- Output cap enforcement (FR32: 9500 char soft limit) ---
if [ -n "$_hint_output" ]; then
    _len=$(printf '%s' "$_hint_output" | wc -c) || _len=0
    if [ "$_len" -gt 9500 ] 2>/dev/null; then
        _hint_output=$(printf '%s' "$_hint_output" | head -c 9400)
        _hint_output="${_hint_output}
... (truncated — run trw_before_edit_hint for full context)"
    fi
    printf '%s\n' "$_hint_output"
fi

exit 0
