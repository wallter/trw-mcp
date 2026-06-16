#!/bin/sh
# GM-01 Gemini BeforeTool hook: trw-distill edit hint.
#
# PRD-DIST-2459 FR-3. Mirrors the Claude Code CC-03 pre-tool-distill-hint.sh
# but adapts to Gemini CLI's BeforeTool JSON protocol:
#   - Reads the BeforeTool stdin JSON (tool_name + tool_input.file_path).
#   - Emits a Gemini hook response on stdout as JSON:
#       {"hookSpecificOutput": {"hookEventName": "BeforeTool",
#                               "additionalContext": "<hint text>"}}
#     (per docs/research/providers/gemini/integration-research.md §5 —
#      additionalContext is the documented hint-injection field; it is wrapped
#      under hookSpecificOutput, the canonical Gemini envelope.)
#   - NEVER denies / NEVER exits 2 — advisory only. No {"decision":"deny"}.
#   - On absent sidecar / missing trw_mcp / timeout / no hint: clean no-op
#     (no stdout, exit 0) so Gemini proceeds unchanged.
#
# Reuses the SAME activation gate (cc03_hook_enabled), the SAME sidecar contract
# (compute_before_edit_hint — distill-unaware, reads risk-report-sidecar/v0),
# the SAME skip-extensions allowlist, and the SAME 180s per-file debounce (FR-6).
#
# POSIX sh compatible. Hook latency budget: Python subprocess <= 2500ms.

set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-distill-hint.sh
. "$_hook_dir/lib-distill-hint.sh" 2>/dev/null || exit 0

# --- Read JSON payload from stdin (Gemini BeforeTool) ---
_payload=$(cat 2>/dev/null) || exit 0

# Gemini BeforeTool stdin carries the model's tool arguments under tool_input.
# Write/edit tools expose the target path as file_path / absolute_path / path.
_file_path=""
_tool_name=""

if command -v jq >/dev/null 2>&1; then
    _tool_name=$(printf '%s' "$_payload" | jq -r '.tool_name // empty' 2>/dev/null) || true
    _file_path=$(printf '%s' "$_payload" | jq -r '.tool_input.file_path // .tool_input.absolute_path // .tool_input.path // empty' 2>/dev/null) || true
else
    # grep/sed fallback — match the first file_path / absolute_path / path key.
    _tool_name=$(printf '%s' "$_payload" | grep -o '"tool_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"tool_name"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
    _file_path=$(printf '%s' "$_payload" | grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"file_path"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
    if [ -z "$_file_path" ]; then
        _file_path=$(printf '%s' "$_payload" | grep -o '"absolute_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"absolute_path"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
    fi
    if [ -z "$_file_path" ]; then
        _file_path=$(printf '%s' "$_payload" | grep -o '"path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"path"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
    fi
fi

# --- Skip 1: shared opt-in gate (FR-6) ---
if ! _get_cc03_enabled; then
    exit 0
fi

# --- Skip 2: no file_path (non-file tool, e.g. run_shell_command) ---
[ -n "$_file_path" ] || exit 0

# --- Skip 3: safe extension allowlist ---
if _is_safe_extension "$_file_path"; then
    exit 0
fi

# --- Skip 4: debounce (same file within last 180s) ---
_repo="${TRW_PROJECT_DIR:-$(pwd)}"
_debounce_dir="${_repo}/.trw/context/gm01-debounce"
_safe_name=$(printf '%s' "$_file_path" | tr '/' '_' | tr -cd 'a-zA-Z0-9_.-')
if [ -d "$_debounce_dir" ]; then
    _debounce_file="${_debounce_dir}/${_safe_name}.ts"
    if [ -f "$_debounce_file" ]; then
        _now=$(date +%s 2>/dev/null) || _now=0
        _last=$(cat "$_debounce_file" 2>/dev/null) || _last=0
        _diff=$(( _now - _last ))
        if [ "$_diff" -lt 180 ] 2>/dev/null; then
            exit 0
        fi
    fi
    date +%s > "$_debounce_file" 2>/dev/null || true
else
    mkdir -p "$_debounce_dir" 2>/dev/null || true
    date +%s > "${_debounce_dir}/${_safe_name}.ts" 2>/dev/null || true
fi

# --- T0 JSON beacon: the always-valid fallback envelope (advisory only) ---
_emit_t0_beacon() {
    printf '%s\n' '{"hookSpecificOutput": {"hookEventName": "BeforeTool", "additionalContext": "[TRW] Distill intelligence available — run trw_before_edit_hint for details."}}'
}

# --- Resolve Python path; no python => T0 beacon (still advisory, never blocks) ---
_py=$(_get_python_path 2>/dev/null) || {
    _emit_t0_beacon
    exit 0
}

# --- Compute hint via compute_before_edit_hint (distill-unaware) and emit ---
# The Python emits the full Gemini JSON envelope itself via json.dumps so the
# hint text is correctly escaped. On no hint it prints nothing; the shell then
# falls back to the T0 beacon below. Timeout matches the 3000ms hook budget.
_response=$(
    PYTHONDONTWRITEBYTECODE=1 PYTHONOPTIMIZE=1 \
    TRW_GM01_FILE_PATH="$_file_path" \
    timeout 2.8 "$_py" -c '
import os, json
try:
    from trw_mcp.tools.before_edit_hint import compute_before_edit_hint
    from trw_mcp.channels.claude_code._hook_helpers import (
        format_t0_beacon, format_t1_hint, format_t2_hint,
    )
    fp = os.environ.get("TRW_GM01_FILE_PATH", "")
    result = compute_before_edit_hint(file_path=fp)
    hint = result.distill_hint
    learnings = [{"summary": l.summary} for l in result.learnings]
    if hint and result.distill_status == "hint_available":
        text = format_t2_hint(
            file_path=fp,
            risk_score=hint.risk_score,
            hotspot_warnings=hint.hotspot_warnings,
            co_change_neighbors=hint.co_change_neighbors,
            inferred_tests=hint.inferred_tests,
        )
    elif learnings:
        text = format_t1_hint(learnings)
    else:
        text = format_t0_beacon()
    if text:
        if len(text) > 9400:
            text = text[:9400] + "\n... (truncated — run trw_before_edit_hint for full context)"
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "BeforeTool",
                "additionalContext": text,
            }
        }))
except Exception:
    # Fail-soft: emit nothing so Gemini proceeds unchanged (never block).
    pass
' 2>/dev/null
) || {
    # Timeout or error: fall back to the T0 beacon JSON (advisory, never blocks).
    _emit_t0_beacon
    exit 0
}

# --- Emit the JSON response; empty (e.g. trw_mcp unimportable) => T0 beacon ---
if [ -n "$_response" ]; then
    printf '%s\n' "$_response"
else
    _emit_t0_beacon
fi

exit 0
