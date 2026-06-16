#!/bin/sh
# CUR-06 Cursor preToolUse hook: trw-distill edit hint.
#
# PRD-DIST-2459 FR-4. Mirrors the Claude Code CC-03 pre-tool-distill-hint.sh
# and the Gemini GM-01 trw-before-tool-hint.sh, but adapts to Cursor's
# preToolUse JSON protocol:
#   - Reads the preToolUse stdin JSON (tool_name + tool_input.file_path).
#   - Emits a Cursor permission response on stdout as JSON:
#       {"permission": "allow", "agent_message": "<hint text>"}
#     (per docs/research/providers/cursor/cursor-cli/integration-research.md §4 —
#      the preToolUse permission shape supports an optional agent_message field
#      shown to the AI agent; "permission":"allow" is the non-blocking observer
#      decision. NO additionalContext field exists for preToolUse — agent_message
#      is the documented advisory-context channel.)
#   - NEVER denies / NEVER exits 2 / NEVER emits "permission":"deny" — advisory
#     only. exit 2 is Cursor's deny signal, so it is never used.
#   - On absent sidecar / missing trw_mcp / timeout / no hint: emits a clean
#     {"permission":"allow"} (exit 0) so Cursor proceeds unchanged and the
#     companion observer hook's allow decision is never contradicted.
#
# This hook CHAINS alongside the existing observer trw-pre-tool-use.sh in the
# preToolUse array — both run, both are non-blocking. Neither displaces the
# other (FR-4: chain, do not replace).
#
# Reuses the SAME activation gate (cc03_hook_enabled), the SAME sidecar contract
# (compute_before_edit_hint — distill-unaware, reads risk-report-sidecar/v0),
# the SAME skip-extensions allowlist, and the SAME 180s per-file debounce (FR-6).
#
# POSIX sh compatible. Hook latency budget: Python subprocess <= 2500ms.

set -e

_emit_allow_noop() {
    printf '%s\n' '{"permission": "allow"}'
}

# Emit a plain allow and exit 0. Clears the EXIT trap first so the controlled
# exit never double-prints via the trap.
_allow_and_exit() {
    trap - EXIT
    _emit_allow_noop
    exit 0
}

# On ANY unexpected/error exit (set -e), emit a plain allow and exit 0 so the
# action never blocks and the observer's allow decision is honored.
trap '_emit_allow_noop 2>/dev/null || true; exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-distill-hint.sh
. "$_hook_dir/lib-distill-hint.sh" 2>/dev/null || _allow_and_exit

_repo="$(_resolve_project_dir)"

# --- Read JSON payload from stdin (Cursor preToolUse) ---
_payload=$(cat 2>/dev/null) || _allow_and_exit

# Cursor preToolUse stdin carries the model's tool arguments under tool_input.
# Write/edit tools expose the target path as file_path / path / target_file.
_file_path=""
_tool_name=""

if command -v jq >/dev/null 2>&1; then
    _tool_name=$(printf '%s' "$_payload" | jq -r '.tool_name // empty' 2>/dev/null) || true
    _file_path=$(printf '%s' "$_payload" | jq -r '.tool_input.file_path // .tool_input.path // .tool_input.target_file // empty' 2>/dev/null) || true
else
    # grep/sed fallback — match the first file_path / path / target_file key.
    _tool_name=$(printf '%s' "$_payload" | grep -o '"tool_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"tool_name"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
    _file_path=$(printf '%s' "$_payload" | grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"file_path"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
    if [ -z "$_file_path" ]; then
        _file_path=$(printf '%s' "$_payload" | grep -o '"path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"path"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
    fi
    if [ -z "$_file_path" ]; then
        _file_path=$(printf '%s' "$_payload" | grep -o '"target_file"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"target_file"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
    fi
fi

# --- Skip 1: shared opt-in gate (FR-6) => plain allow no-op ---
if ! _get_cc03_enabled; then
    _allow_and_exit
fi

# --- Skip 2: no file_path (non-file tool, e.g. terminal) => plain allow ---
[ -n "$_file_path" ] || _allow_and_exit

# --- Skip 3: safe extension allowlist => plain allow ---
if _is_safe_extension "$_file_path"; then
    _allow_and_exit
fi

# --- Skip 4: debounce (same file within last 180s) => plain allow ---
_debounce_dir="${_repo}/.trw/context/cur06-debounce"
_safe_name=$(printf '%s' "$_file_path" | tr '/' '_' | tr -cd 'a-zA-Z0-9_.-')
if [ -d "$_debounce_dir" ]; then
    _debounce_file="${_debounce_dir}/${_safe_name}.ts"
    if [ -f "$_debounce_file" ]; then
        _now=$(date +%s 2>/dev/null) || _now=0
        _last=$(cat "$_debounce_file" 2>/dev/null) || _last=0
        _diff=$(( _now - _last ))
        if [ "$_diff" -lt 180 ] 2>/dev/null; then
            _allow_and_exit
        fi
    fi
    date +%s > "$_debounce_file" 2>/dev/null || true
else
    mkdir -p "$_debounce_dir" 2>/dev/null || true
    date +%s > "${_debounce_dir}/${_safe_name}.ts" 2>/dev/null || true
fi

# --- Resolve Python path; no python => plain allow (still advisory) ---
_py=$(_get_python_path 2>/dev/null) || _allow_and_exit

# --- Compute hint via compute_before_edit_hint (distill-unaware) and emit ---
# The Python emits the full Cursor JSON envelope itself via json.dumps so the
# hint text is correctly escaped into agent_message. On no hint it prints the
# plain allow envelope. Timeout matches the hook latency budget.
_response=$(
    PYTHONDONTWRITEBYTECODE=1 PYTHONOPTIMIZE=1 \
    TRW_CUR06_FILE_PATH="$_file_path" \
    timeout 2.5 "$_py" -c '
import os, json
# Always-valid fallback: a non-blocking allow with no agent_message.
_fallback = {"permission": "allow"}
try:
    from trw_mcp.tools.before_edit_hint import compute_before_edit_hint
    from trw_mcp.channels.claude_code._hook_helpers import (
        format_t0_beacon, format_t1_hint, format_t2_hint,
    )
    fp = os.environ.get("TRW_CUR06_FILE_PATH", "")
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
        print(json.dumps({"permission": "allow", "agent_message": text}))
    else:
        print(json.dumps(_fallback))
except Exception:
    # Fail-soft: emit a plain allow so Cursor proceeds unchanged (never block).
    print(json.dumps(_fallback))
' 2>/dev/null
) || {
    # Timeout or error: fall back to the plain allow envelope (never blocks).
    _allow_and_exit
}

# --- Emit the JSON response; empty (e.g. python crashed) => plain allow ---
trap - EXIT
if [ -n "$_response" ]; then
    printf '%s\n' "$_response"
else
    _emit_allow_noop
fi

exit 0
