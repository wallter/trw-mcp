#!/bin/sh
# CUR-06 Cursor preToolUse hook: trw-distill edit hint.
#
# PRD-DIST-2459 FR-4. Mirrors the Claude Code CC-03 pre-tool-distill-hint.sh
# and the Gemini GM-01 trw-before-tool-hint.sh, but adapts to Cursor's
# preToolUse JSON protocol:
#   - Reads the preToolUse stdin JSON (tool_name + tool_input.file_path).
#   - Emits a Cursor permission response on stdout as JSON:
#       {"permission": "allow", "agent_message": "<hint text>"}
#     (Cursor's preToolUse permission shape supports an optional agent_message
#      field shown to the AI agent; "permission":"allow" is the non-blocking
#      observer decision. NO additionalContext field exists for preToolUse —
#      agent_message is the documented advisory-context channel.)
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

# jq only (T29): the hint is advisory, so a jq-less host simply allows.
command -v jq >/dev/null 2>&1 || _allow_and_exit
_tool_name=$(printf '%s' "$_payload" | jq -r '.tool_name // empty' 2>/dev/null) || true
_file_path=$(printf '%s' "$_payload" | jq -r '.tool_input.file_path // .tool_input.path // .tool_input.target_file // empty' 2>/dev/null) || true

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
# Sanitized name PLUS a checksum of the exact path. The sanitizer alone is
    # lossy -- it deletes every character outside [A-Za-z0-9_.-], so 'src/a.py'
    # and 'src/\u03b1.py' both collapse to 'src_.py'-ish forms and the second file
    # edited was silently debounced for 180s as if it were the first. cksum is
    # POSIX, present everywhere this runs, and costs no interpreter start.
    _safe_name=$(printf '%s' "$_file_path" | tr '/' '_' | tr -cd 'a-zA-Z0-9_.-')
    _path_ck=$(printf '%s' "$_file_path" | cksum | cut -d' ' -f1)
    _safe_name="${_safe_name}-${_path_ck}"
_debounce_file="${_debounce_dir}/${_safe_name}.ts"
# PRD-SEC/RC8: _trw_safe_read/_trw_safe_write (lib-distill-hint.sh, sourced
# above) treat a symlinked debounce marker as absent and never write through
# one.
_last=$(_trw_safe_read "$_debounce_file") || _last=0
if [ -n "$_last" ]; then
    _now=$(date +%s 2>/dev/null) || _now=0
    _diff=$(( _now - _last ))
    if [ "$_diff" -lt 180 ] 2>/dev/null; then
        _allow_and_exit
    fi
fi
date +%s | _trw_safe_write "$_debounce_file" || true

# --- Resolve Python path; no python => plain allow (still advisory) ---
_py=$(_get_python_path 2>/dev/null) || _allow_and_exit

# --- Portable 2.5s bound for the hint subprocess (FR30) -----------------------
# This call used to read `timeout 2.5 "$_py" -c ...`. `timeout` is GNU
# coreutils and macOS ships neither it nor `gtimeout`, so on every Mac the
# command failed with 127 BEFORE the interpreter started: the fallback below
# fired on every single edit, the hint program never ran, and the CC-04
# correlation record reported a `timeout_fallback` for a timeout that had not
# happened (verified 2026-09-17). The deadline now lives where it is portable:
#   1. INSIDE the program (SIGALRM -> os._exit(124), installed as its first
#      statement) — the truthful bound whenever $_py is really an interpreter;
#   2. this function as the OUTER backstop for what an alarm cannot cover, an
#      interpreter that hangs before it runs our code. It delegates to
#      `timeout`/`gtimeout` where the box has one and uses a POSIX watchdog
#      (background child + `sleep` + `kill`) where it does not.
# Usage: _trw_bounded_python <seconds> <VAR=value ...> "$_py" -c <program>
_TRW_TIMEOUT_BIN=""
for _bp_cand in timeout gtimeout; do
    if command -v "$_bp_cand" >/dev/null 2>&1; then
        _TRW_TIMEOUT_BIN=$(command -v "$_bp_cand")
        break
    fi
done

_trw_bounded_python() {
    _bp_secs="$1"
    shift
    _bp_rc=0
    # The bounded child writes to a FILE, never straight into the caller's
    # command substitution: killing it does not kill a grandchild it left
    # behind (a wrapper script's `sleep`, a forked worker), and any survivor
    # holding the substitution pipe open makes the caller wait for the very
    # runtime this bound exists to cut -- measured 10s for a 1s bound under
    # dash and bash 3.2 before the file was introduced.
    _bp_out=$(mktemp 2>/dev/null) || _bp_out=""
    if [ -z "$_bp_out" ]; then
        env "$@" || _bp_rc=$?
        return $_bp_rc
    fi
    if [ -n "$_TRW_TIMEOUT_BIN" ]; then
        "$_TRW_TIMEOUT_BIN" "$_bp_secs" env "$@" > "$_bp_out" || _bp_rc=$?
    else
        env "$@" > "$_bp_out" &
        _bp_pid=$!
        ( sleep "$_bp_secs"; kill -TERM "$_bp_pid" ) >/dev/null 2>&1 &
        _bp_watch=$!
        wait "$_bp_pid" || _bp_rc=$?
        kill -TERM "$_bp_watch" 2>/dev/null || true
    fi
    cat "$_bp_out" 2>/dev/null || true
    rm -f "$_bp_out" 2>/dev/null || true
    return $_bp_rc
}

# --- Compute hint via compute_before_edit_hint (distill-unaware) and emit ---
# The Python emits the full Cursor JSON envelope itself via json.dumps so the
# hint text is correctly escaped into agent_message. On no hint it prints the
# plain allow envelope. The deadline matches the hook latency budget and is
# enforced portably by _trw_bounded_python (see below).
# TRW_EMBEDDINGS_ENABLED=false: a fresh interpreter per edit pays the full
# embedding cold start (measured 14.48s: torch + sentence-transformers + MiniLM
# load), which no PreToolUse budget can cover, while the sidecar read this hook
# exists for costs 3ms. Lexical recall keeps T1 alive at ~0.44s. Full
# measurement table: claude_code/hooks/pre-tool-distill-hint.sh.
_response=$(
    _trw_bounded_python 2.5 \
    PYTHONDONTWRITEBYTECODE=1 PYTHONOPTIMIZE=1 \
    TRW_EMBEDDINGS_ENABLED=false \
    TRW_CUR06_FILE_PATH="$_file_path" \
    "$_py" -c '
import os, json
# The 2.5s budget belongs to THIS interpreter, because `timeout` is a GNU binary
# macOS does not ship (see _trw_bounded_python in the calling hook). os._exit,
# never an exception: the handler below would otherwise record
# exception_fallback for an event that is a genuine timeout. 124 is what
# `timeout` returned, so the shell fallback path is unchanged. 2.4s rather than
# 2.5 so this truthful in-process bound wins the race against the outer
# backstop whenever the interpreter is alive to answer.
try:
    import signal
    def _trw_on_deadline(_signum, _frame):
        os._exit(124)
    signal.signal(signal.SIGALRM, _trw_on_deadline)
    signal.setitimer(signal.ITIMER_REAL, 2.4)
except Exception:
    # A box without SIGALRM keeps the outer shell backstop; it does not lose
    # the hint. Nothing is swallowed here: the bound below is still enforced.
    pass
# Always-valid fallback: a non-blocking allow with no agent_message.
_fallback = {"permission": "allow"}
try:
    from trw_mcp.tools._before_edit_hint_core import compute_before_edit_hint
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
            text = text[:9400] + "\n... (truncated — run trw_code(mode=\"hint\") for full context)"
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

# --- Session-scoped identical-hint dedup (PRD-CORE-301 cut 2) ---
# The 180s debounce above bounds frequency; this bounds REPEATED, unchanged
# agent_message content once the debounce window has lapsed. Never suppresses
# a hint that changed, or the first hint for a file.
if [ -n "$_response" ]; then
    _dedup_text=$(printf '%s' "$_response" | jq -r '.agent_message // empty' 2>/dev/null) || _dedup_text=""
    if [ -n "$_dedup_text" ] && _distill_hint_already_seen "$_repo" "$_file_path" "$_dedup_text"; then
        _response='{"permission": "allow"}'
    fi
fi

# --- Emit the JSON response; empty (e.g. python crashed) => plain allow ---
trap - EXIT
if [ -n "$_response" ]; then
    printf '%s\n' "$_response"
else
    _emit_allow_noop
fi

exit 0
