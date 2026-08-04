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
    # Sanitized name PLUS a checksum of the exact path. The sanitizer alone is
    # lossy -- it deletes every character outside [A-Za-z0-9_.-], so 'src/a.py'
    # and 'src/\u03b1.py' both collapse to 'src_.py'-ish forms and the second file
    # edited was silently debounced for 180s as if it were the first. cksum is
    # POSIX, present everywhere this runs, and costs no interpreter start.
    _safe_name=$(printf '%s' "$_file_path" | tr '/' '_' | tr -cd 'a-zA-Z0-9_.-')
    _path_ck=$(printf '%s' "$_file_path" | cksum | cut -d' ' -f1)
    _safe_name="${_safe_name}-${_path_ck}"
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
    # Sanitized name PLUS a checksum of the exact path. The sanitizer alone is
    # lossy -- it deletes every character outside [A-Za-z0-9_.-], so 'src/a.py'
    # and 'src/\u03b1.py' both collapse to 'src_.py'-ish forms and the second file
    # edited was silently debounced for 180s as if it were the first. cksum is
    # POSIX, present everywhere this runs, and costs no interpreter start.
    _safe_name=$(printf '%s' "$_file_path" | tr '/' '_' | tr -cd 'a-zA-Z0-9_.-')
    _path_ck=$(printf '%s' "$_file_path" | cksum | cut -d' ' -f1)
    _safe_name="${_safe_name}-${_path_ck}"
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

# Write a dependency-free provisional CC-04 record before the bounded
# intelligence subprocess. If the 2.5s advisory budget expires, correlation is
# still preserved truthfully as a T0 timeout fallback; a successful computation
# overwrites this record below with its final tier/status. Environment variables
# keep untrusted hook fields out of Python source interpolation.
if [ -n "$_tool_use_id" ]; then
    TRW_CC04_HINTS_DIR="$_hints_dir" \
    TRW_CC04_TOOL_USE_ID="$_tool_use_id" \
    TRW_CC04_FILE_PATH="$_file_path" \
    "$_py" -c '
import datetime, json, os, pathlib, re
tool_use_id = os.environ["TRW_CC04_TOOL_USE_ID"]
if re.fullmatch(r"[A-Za-z0-9_.-]+", tool_use_id):
    hints_dir = pathlib.Path(os.environ["TRW_CC04_HINTS_DIR"])
    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "file_path": os.environ["TRW_CC04_FILE_PATH"],
        "tier": "T0",
        "hint_emitted": True,
        "tokens_emitted": 9,
        "distill_status": "timeout_fallback",
        "tool_use_id": tool_use_id,
        "outcome_captured": False,
        "was_edited": None,
        "edit_survived": None,
        "test_outcome": "unknown",
        "hint_acknowledged": None,
    }
    (hints_dir / f"{tool_use_id}.json").write_text(json.dumps(record), encoding="utf-8")
' >/dev/null 2>&1 || true
fi

# TRW_EMBEDDINGS_ENABLED=false is load-bearing, not a tuning preference.
# Measured on a warm dev box (7 runs each, seconds):
#   embedding cold start in a FRESH process   14.48  (torch 1.76 + sentence-
#                                                    transformers 5.95 + MiniLM
#                                                    load 6.56 + encode 0.22)
#   compute_before_edit_hint, embeddings ON   14.2 - 14.6
#   compute_before_edit_hint, embeddings OFF   0.68 - 1.04
#   the sidecar read this hook exists for      0.003
# Every PreToolUse call spawns a NEW interpreter, so the model load is paid in
# full every time and is never amortized. Against the 2.5s budget that made
# distill_status="timeout_fallback" the only reachable outcome — T1 and T2 were
# unreachable here, and the T2 work is 3ms. Raising the budget is not the fix:
# it is capped by the 3000ms registered hook timeout (NFR06), and covering a
# 14.5s model load would add ~15s of latency to EVERY edit. Lexical recall still
# returns learnings (measured 0.44s first query, 0.03s after), so T1 survives.
# Scope is this subprocess only — the long-lived MCP server keeps hybrid recall,
# where the model is warm and a query costs 0.23s.
_hint_output=$(
    PYTHONDONTWRITEBYTECODE=1 PYTHONOPTIMIZE=1 \
    TRW_EMBEDDINGS_ENABLED=false \
    TRW_CC04_HINTS_DIR="$_hints_dir" \
    TRW_CC04_TOOL_USE_ID="$_tool_use_id" \
    TRW_CC04_FILE_PATH="$_file_path" \
    timeout 2.5 "$_py" -c '
# SINGLE-quoted on purpose. This program used to be double-quoted, so ${_file_path}
# — a model-controlled PreToolUse field — was spliced into Python SOURCE. A payload
# with file_path = x.py"+__import__("os").system("...")+".py executed as the
# developer, under a hook that needs no tool approval, and the hook still printed
# the ordinary beacon and exited 0. Reproduced before the fix; see CHANGELOG.
#
# The env vars below were already being exported for this subprocess and simply
# were not used on this path. The single quoting is what makes the mistake
# unrepeatable: no $ can be expanded here, so a future edit cannot reintroduce the
# interpolation without visibly changing the quoting. Every Python string below
# therefore uses double quotes. Same invariant as the sibling hooks —
# hooks/cursor/trw-before-edit-hint.sh and git_hooks/trw-post-commit.sh.
import os
try:
    from trw_mcp.tools.before_edit_hint import compute_before_edit_hint
    from trw_mcp.channels.claude_code._hook_helpers import (
        format_t0_beacon, format_t1_hint, format_t2_hint
    )
    file_path = os.environ.get("TRW_CC04_FILE_PATH", "")
    tool_use_id = os.environ.get("TRW_CC04_TOOL_USE_ID", "")
    result = compute_before_edit_hint(file_path=file_path)
    hint = result.distill_hint
    learnings = [{"summary": l.summary} for l in result.learnings]
    if hint and result.distill_status == "hint_available":
        output = format_t2_hint(
            file_path=file_path,
            risk_score=hint.risk_score,
            hotspot_warnings=hint.hotspot_warnings,
            co_change_neighbors=hint.co_change_neighbors,
            inferred_tests=hint.inferred_tests,
        )
        tier = "T2"
    elif learnings:
        output = format_t1_hint(learnings)
        tier = "T1"
    else:
        output = format_t0_beacon()
        tier = "T0"
    # FR29: write hint file with tool_use_id
    if tool_use_id:
        from trw_mcp.channels.claude_code._hook_helpers import write_hint_file
        from pathlib import Path
        write_hint_file(
            hints_dir=Path(os.environ["TRW_CC04_HINTS_DIR"]),
            tool_use_id=tool_use_id,
            file_path=file_path,
            tier=tier,
            hint_emitted=True,
            tokens_emitted=len(output.split()),
            distill_status=result.distill_status,
        )
    print(output)
except Exception:
    # The provisional record written above says 'timeout_fallback' because it
    # is written BEFORE this bounded subprocess starts. Reaching this handler
    # means the subprocess RAN and RAISED — a broken venv ImportError, a
    # version-skew AttributeError, a real bug — which has nothing to do with
    # the 2.5s budget. Leaving the provisional record in place mixes those
    # events into the timeout count, so an operator debugging a low hit rate
    # tunes the timeout for a defect that is not about timing.
    #
    # Deliberately stdlib-only and self-contained: the import that just failed
    # must not be a precondition for recording that it failed. Untrusted hook
    # fields arrive via the environment, never interpolated into source — which
    # is now true of the whole program, not only of this handler.
    try:
        import datetime, json, pathlib, re
        _tuid = os.environ.get("TRW_CC04_TOOL_USE_ID", "")
        if _tuid and re.fullmatch(r"[A-Za-z0-9_.-]+", _tuid):
            _dir = pathlib.Path(os.environ["TRW_CC04_HINTS_DIR"])
            _dir.mkdir(parents=True, exist_ok=True)
            (_dir / (_tuid + ".json")).write_text(json.dumps({
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
                "file_path": os.environ.get("TRW_CC04_FILE_PATH", ""),
                "tier": "T0",
                "hint_emitted": True,
                "tokens_emitted": 9,
                "distill_status": "exception_fallback",
                "tool_use_id": _tuid,
                "outcome_captured": False,
                "was_edited": None,
                "edit_survived": None,
                "test_outcome": "unknown",
                "hint_acknowledged": None,
            }), encoding="utf-8")
    except Exception:
        pass
    print("[TRW] Distill intelligence available — run trw_before_edit_hint for details.")
' 2>/dev/null
) || {
    # Timeout or error: fall back to T0 beacon (FR30, FR31)
    _format_t0_beacon
    exit 0
}

# --- CC-01 background snapshot refresh (PRD-CORE-231 FR01) ---
# _write_distill_snapshot_bg() has been defined in lib-distill-hint.sh since
# PRD-DIST-2405 but had ZERO call sites — the CC-01 memory-snapshot refresh
# never fired. Trigger it only after a real T2 result: a T1/T0 fallback carries
# no new distill intelligence worth snapshotting. Backgrounded and fail-silent,
# so it cannot add latency to (or fail) the PreToolUse call.
case "$_hint_output" in
    *"[TRW Distill Hint — T2]"*)
        _write_distill_snapshot_bg 2>/dev/null || true
        ;;
esac

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
