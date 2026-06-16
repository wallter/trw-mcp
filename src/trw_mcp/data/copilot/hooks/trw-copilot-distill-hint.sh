#!/bin/sh
# C5 Copilot preToolUse hook: trw-distill edit hint (advisory text producer).
#
# PRD-DIST-2459 FR-5. Mirrors the Claude Code CC-03 pre-tool-distill-hint.sh,
# the Gemini GM-01 trw-before-tool-hint.sh, and the Cursor CUR-06
# trw-before-edit-hint.sh, but is adapted to Copilot's adapter-chain model.
#
# CONTRACT (deliberately narrow):
#   - This hook is invoked BY the adapter (trw-copilot-adapter.sh), AFTER the
#     authoritative deliver-gate has already decided allow. The adapter only
#     runs this hook when the deliver-gate allowed; when the deliver-gate
#     blocks (exit 2 -> permissionDecision:deny), this hook is NEVER consulted.
#   - It reads the Copilot preToolUse stdin JSON (toolName + the edit target
#     path under toolArgs/tool_input — Copilot file-editing tools expose the
#     path as filePath/file_path/path/target_file).
#   - It emits ONLY the advisory HINT TEXT on stdout (plain text, possibly
#     empty). It does NOT emit a permissionDecision envelope — the adapter owns
#     the final {"permissionDecision":...} decision so this hook can NEVER flip
#     an allow into a deny (FR-5: distill-hint never converts allow->deny).
#   - NEVER exits 2. NEVER denies. Always exits 0. On absent sidecar / missing
#     trw_mcp / timeout / gate-off / non-code file, it prints NOTHING and
#     exits 0 (the adapter then emits a clean allow with no advisory reason).
#
# Reuses the SAME activation gate (cc03_hook_enabled), the SAME sidecar contract
# (compute_before_edit_hint — distill-unaware, reads risk-report-sidecar/v0),
# the SAME skip-extensions allowlist, and the SAME 180s per-file debounce (FR-6).
#
# POSIX sh compatible. Hook latency budget: Python subprocess <= 2500ms.

set -e

# On ANY unexpected/error exit (set -e), print nothing and exit 0 so the
# adapter's allow decision is honored and nothing ever blocks on the hint path.
trap 'exit 0' EXIT

_emit_nothing_and_exit() {
    trap - EXIT
    exit 0
}

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-copilot-distill-hint.sh
. "$_hook_dir/lib-copilot-distill-hint.sh" 2>/dev/null || _emit_nothing_and_exit

_repo="$(_resolve_project_dir)"

# --- Read JSON payload from stdin (Copilot preToolUse) ---
_payload=$(cat 2>/dev/null) || _emit_nothing_and_exit

# Copilot preToolUse stdin carries the model's tool arguments under toolArgs
# (and, for cross-client compatibility, tool_input). Edit tools expose the
# target path as filePath / file_path / path / target_file.
_file_path=""

if command -v jq >/dev/null 2>&1; then
    _file_path=$(printf '%s' "$_payload" | jq -r '
        .toolArgs.filePath // .toolArgs.file_path // .toolArgs.path // .toolArgs.target_file //
        .tool_input.file_path // .tool_input.path // .tool_input.target_file //
        empty' 2>/dev/null) || true
else
    # grep/sed fallback — match the first filePath / file_path / path / target_file key.
    _file_path=$(printf '%s' "$_payload" | grep -o '"filePath"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"filePath"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
    if [ -z "$_file_path" ]; then
        _file_path=$(printf '%s' "$_payload" | grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"file_path"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
    fi
    if [ -z "$_file_path" ]; then
        _file_path=$(printf '%s' "$_payload" | grep -o '"path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"path"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
    fi
    if [ -z "$_file_path" ]; then
        _file_path=$(printf '%s' "$_payload" | grep -o '"target_file"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"target_file"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
    fi
fi

# --- Skip 1: shared opt-in gate (FR-6) => print nothing ---
if ! _get_cc03_enabled; then
    _emit_nothing_and_exit
fi

# --- Skip 2: no file_path (non-file tool, e.g. shell) => print nothing ---
[ -n "$_file_path" ] || _emit_nothing_and_exit

# --- Skip 3: safe extension allowlist => print nothing ---
if _is_safe_extension "$_file_path"; then
    _emit_nothing_and_exit
fi

# --- Skip 4: debounce (same file within last 180s) => print nothing ---
_debounce_dir="${_repo}/.trw/context/c5-copilot-debounce"
_safe_name=$(printf '%s' "$_file_path" | tr '/' '_' | tr -cd 'a-zA-Z0-9_.-')
if [ -d "$_debounce_dir" ]; then
    _debounce_file="${_debounce_dir}/${_safe_name}.ts"
    if [ -f "$_debounce_file" ]; then
        _now=$(date +%s 2>/dev/null) || _now=0
        _last=$(cat "$_debounce_file" 2>/dev/null) || _last=0
        _diff=$(( _now - _last ))
        if [ "$_diff" -lt 180 ] 2>/dev/null; then
            _emit_nothing_and_exit
        fi
    fi
    date +%s > "$_debounce_file" 2>/dev/null || true
else
    mkdir -p "$_debounce_dir" 2>/dev/null || true
    date +%s > "${_debounce_dir}/${_safe_name}.ts" 2>/dev/null || true
fi

# --- Resolve Python path; no python => print nothing (still advisory) ---
_py=$(_get_python_path 2>/dev/null) || _emit_nothing_and_exit

# --- Compute hint via compute_before_edit_hint (distill-unaware) and print ---
# The Python prints ONLY the advisory hint text (no JSON envelope). On no hint
# it prints nothing. Timeout matches the hook latency budget.
_hint_text=$(
    PYTHONDONTWRITEBYTECODE=1 PYTHONOPTIMIZE=1 \
    TRW_C5_FILE_PATH="$_file_path" \
    timeout 2.5 "$_py" -c '
import os, sys
try:
    from trw_mcp.tools.before_edit_hint import compute_before_edit_hint
    from trw_mcp.channels.claude_code._hook_helpers import (
        format_t0_beacon, format_t1_hint, format_t2_hint,
    )
    fp = os.environ.get("TRW_C5_FILE_PATH", "")
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
        sys.stdout.write(text)
except Exception:
    # Fail-soft: print nothing so the adapter emits a clean allow (never block).
    pass
' 2>/dev/null
) || {
    # Timeout or error: print nothing (never blocks; advisory only).
    _emit_nothing_and_exit
}

# --- Print the advisory hint text (may be empty); always exit 0 ---
trap - EXIT
if [ -n "$_hint_text" ]; then
    printf '%s' "$_hint_text"
fi

exit 0
