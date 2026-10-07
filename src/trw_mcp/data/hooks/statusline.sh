#!/bin/sh
# TRW Claude Code statusLine (PRD-CORE-354 FR05).
#
# Claude Code pipes a session JSON object to stdin and renders our stdout as the
# status line. Contract: print EXACTLY ONE line and exit 0 in every case; a
# status line must never break the UI. Read-only: the only write is the
# status cache the CLI itself maintains.
#
# CLI resolution order: sibling `trw-mcp` of the absolute launcher in the
# project's .mcp.json (needs python3) -> $CLAUDE_PROJECT_DIR/.venv/bin/trw-mcp
# -> PATH.

FALLBACK="TRW · status unavailable"

# One isolated python3 (-I: never imports from the checkout) reads the stdin
# JSON and the project's .mcp.json and prints three lines: session_id,
# project_dir, launcher. Any failure prints nothing.
_PROBE_PY='
import json, os, sys
def walk(n, key):
    if isinstance(n, dict):
        if isinstance(n.get(key), str):
            return n[key]
        for v in n.values():
            r = walk(v, key)
            if r:
                return r
    return ""
try:
    data = json.loads(sys.argv[1])
except Exception:
    data = {}
proj = sys.argv[2] or walk(data, "project_dir")
launcher = ""
try:
    mcp = proj + "/.mcp.json"
    if os.path.isfile(mcp):
        launcher = json.load(open(mcp))["mcpServers"]["trw"]["command"]
except Exception:
    pass
for v in (walk(data, "session_id"), proj, launcher):
    print(str(v).replace("\n", " "))
'

_resolve_cli() {
    # $1 project dir, $2 absolute launcher from .mcp.json ("" when unknown)
    _launcher="$2"
    case "$_launcher" in
        /*)
            if [ -x "$(dirname "$_launcher")/trw-mcp" ]; then
                printf '%s' "$(dirname "$_launcher")/trw-mcp"
                return 0
            fi
            ;;
    esac
    if [ -x "$1/.venv/bin/trw-mcp" ]; then
        printf '%s' "$1/.venv/bin/trw-mcp"
        return 0
    fi
    command -v trw-mcp 2>/dev/null
}

# Run "$@" with a hard wall-clock cap (macOS ships no `timeout`): a hung CLI
# must not freeze the status line. Prints the command's stdout; non-zero on
# timeout or failure.
_run_bounded() {
    _tmp=$(mktemp 2>/dev/null) || return 1
    _limit="${TRW_STATUSLINE_TIMEOUT_TICKS:-30}"
    case "$_limit" in
        '' | *[!0-9]* | 0 | 0[0-9]*) _limit=30 ;;
    esac
    "$@" </dev/null >"$_tmp" 2>/dev/null &
    _pid=$!
    _ticks=0
    while kill -0 "$_pid" 2>/dev/null; do
        if [ "$_ticks" -ge "$_limit" ]; then
            kill "$_pid" 2>/dev/null
            rm -f "$_tmp"
            return 1
        fi
        sleep 0.1
        _ticks=$((_ticks + 1))
    done
    wait "$_pid"
    _rc=$?
    cat "$_tmp"
    rm -f "$_tmp"
    return "$_rc"
}

# Portable mtime in epoch seconds: GNU `stat -c`, then BSD/macOS `stat -f`. GNU first: GNU reads `-f` as
# --file-system and prints a filesystem report before failing on "%m", which polluted the value; BSD's
# `stat -c` fails with nothing on stdout.
_mtime() {
    stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null
}

# Print the cached status line for session $1 when fresh (< 5 s); non-zero otherwise.
_fast_line() {
    case "$1" in
        '' | *[!A-Za-z0-9._-]*) return 1 ;;
    esac
    _f=".trw/runtime/status/$1.line"
    [ -f "$_f" ] && [ ! -L "$_f" ] || return 1
    _m=$(_mtime "$_f")
    case "$_m" in
        '' | *[!0-9]*) return 1 ;;
    esac
    _age=$(($(date +%s) - _m))
    [ "$_age" -ge 0 ] && [ "$_age" -lt 5 ] || return 1
    _fl=$(sed -n '/[^[:space:]]/{p;q;}' "$_f" 2>/dev/null | tr -d '\000-\010\013-\037')
    [ -n "$_fl" ] || return 1
    printf '%s\n' "$_fl"
}

_main() {
    _input=""
    [ -t 0 ] || _input=$(cat 2>/dev/null)
    _sid=""
    _proj="${CLAUDE_PROJECT_DIR:-}"
    _launcher_cfg=""
    if command -v python3 >/dev/null 2>&1; then
        _probe=$(_run_bounded python3 -I -c "$_PROBE_PY" "$_input" "$_proj")
        _sid=$(printf '%s\n' "$_probe" | sed -n 1p)
        _proj=$(printf '%s\n' "$_probe" | sed -n 2p)
        _launcher_cfg=$(printf '%s\n' "$_probe" | sed -n 3p)
    else
        # Cache key only: a guessed session id degrades to a shared cache entry.
        _sid=$(printf '%s' "$_input" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)
    fi
    [ -n "$_proj" ] || _proj=$(pwd)
    cd "$_proj" 2>/dev/null || return 1

    # Fast path: the CLI (python startup, ~400 ms) also writes <sid>.line next to
    # <sid>.json; a copy younger than 5 s is printed as-is. The id is validated
    # before it touches a path, and only a regular file is read.
    if _fast_line "$_sid"; then
        return 0
    fi

    _cli=$(_resolve_cli "$_proj" "$_launcher_cfg")
    [ -n "$_cli" ] || return 1

    if [ -n "$_sid" ]; then
        _out=$(_run_bounded "$_cli" local status --format line --session-id "$_sid" --cache-ttl 5) || return 1
    else
        _out=$(_run_bounded "$_cli" local status --format line --cache-ttl 5) || return 1
    fi
    # Exactly one printable line: first non-empty line, control characters stripped.
    _line=$(printf '%s\n' "$_out" | sed -n '/[^[:space:]]/{p;q;}' | tr -d '\000-\010\013-\037')
    [ -n "$_line" ] || return 1
    printf '%s\n' "$_line"
}

# The one hooks switch (lib-trw.sh): with hooks_enabled=false TRW stays silent,
# so the status line prints nothing at all, before reading stdin or writing.
_gate_root="${CLAUDE_PROJECT_DIR:-$(pwd)}"
if grep -qx 'hooks_enabled=false' "$_gate_root/.trw/runtime/hook-flags" 2>/dev/null; then
    exit 0
fi

_main || printf '%s\n' "$FALLBACK"
exit 0
