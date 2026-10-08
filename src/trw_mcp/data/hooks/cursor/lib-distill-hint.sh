#!/bin/sh
# CUR-06 shared helper library for the Cursor preToolUse distill hint hook.
#
# PRD-DIST-2459 FR-4. Mirrors the Claude Code lib-distill-hint.sh contract
# (config gate, python-path resolution, safe-extension allowlist) so the
# Cursor hint hook reuses the SAME activation gate (cc03_hook_enabled), the
# SAME sidecar contract (read via compute_before_edit_hint), and the SAME
# skip-extensions allowlist — no per-profile fork (FR-6).
#
# This is a sibling of the Claude/Gemini libs (NOT a copy imported by another
# client's path) so the Cursor hook ships standalone. POSIX sh only — no
# bashisms.
#
# Provides:
#   _get_python_path <dir>       — resolves the interpreter hooks start (PRD-FIX-155)
#   _read_json_field()           — reads a payload field (jq, else python)
#   _read_trw_config_field()     — reads a top-level scalar from .trw/config.yaml
#   _read_trw_nested_config_field() — reads a one-level-nested scalar
#   _get_cc03_enabled()          — checks the shared cc03_hook_enabled gate (opt-in)
#   _is_safe_extension()         — returns 0 if extension should be skipped

# ---------------------------------------------------------------------------
# Project-dir resolution
#
# The Cursor observer hook (trw-pre-tool-use.sh) uses CURSOR_PROJECT_DIR; the
# CC-03 / GM-01 libs use TRW_PROJECT_DIR. Accept either (TRW_PROJECT_DIR wins),
# falling back to the current working directory.
# ---------------------------------------------------------------------------

_resolve_project_dir() {
    if [ -n "${TRW_PROJECT_DIR:-}" ]; then
        printf '%s' "$TRW_PROJECT_DIR"
    elif [ -n "${CURSOR_PROJECT_DIR:-}" ]; then
        printf '%s' "$CURSOR_PROJECT_DIR"
    else
        pwd
    fi
}

# ---------------------------------------------------------------------------
# Python path resolution
# ---------------------------------------------------------------------------

_get_python_path() {
    # Usage: _get_python_path <project_dir>
    # PRD-FIX-155: the one interpreter resolution order, byte-identical in every
    # bundled hook that starts Python (a test pins the copies). First hit wins:
    #   1. <project_dir>/.trw/channels/cc03-python.txt, which init-project and
    #      update-project fill with the interpreter that runs trw-mcp
    #   2. the interpreter in the shebang of `command -v trw-mcp`
    #   3. <project_dir>/.venv/bin/python
    #   4. worktree fallback: in a linked git worktree, .trw is per-worktree and
    #      untracked (steps 1 and 3 above never see it), so fall back to the
    #      MAIN worktree's pointer, then its .venv, resolved via
    #      `git rev-parse --git-common-dir` (one cheap call, only reached
    #      here; a git error or non-worktree checkout just falls through)
    #   5. python3 on PATH, which often cannot import trw_mcp (trw-mcp doctor)
    # A pointer counts only as an ABSOLUTE path to an executable regular file: a
    # relative one would resolve against whatever directory the hook runs in.
    _trw_py=$(cat "$1/.trw/channels/cc03-python.txt" 2>/dev/null) || _trw_py=""
    case "$_trw_py" in /*) [ -f "$_trw_py" ] || _trw_py="" ;; *) _trw_py="" ;; esac
    if [ -z "$_trw_py" ] || [ ! -x "$_trw_py" ]; then
        _trw_py=$(command -v trw-mcp 2>/dev/null) || _trw_py=""
        [ -z "$_trw_py" ] || _trw_py=$(head -n 1 "$_trw_py" 2>/dev/null) || _trw_py=""
        _trw_py=${_trw_py#\#!}
        _trw_py=${_trw_py%% *}
        case "${_trw_py##*/}" in python*) ;; *) _trw_py="" ;; esac
        [ -x "$_trw_py" ] || _trw_py="$1/.venv/bin/python"
    fi
    if [ ! -x "$_trw_py" ]; then
        _trw_common=$(git -C "$1" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || _trw_common=""
        if [ -n "$_trw_common" ]; then
            _trw_main=$(dirname "$_trw_common")
            _trw_py=$(cat "$_trw_main/.trw/channels/cc03-python.txt" 2>/dev/null) || _trw_py=""
            case "$_trw_py" in /*) [ -f "$_trw_py" ] || _trw_py="" ;; *) _trw_py="" ;; esac
            [ -x "$_trw_py" ] || _trw_py="$_trw_main/.venv/bin/python"
        fi
    fi
    if [ -x "$_trw_py" ]; then
        printf '%s' "$_trw_py"
    elif command -v python3 >/dev/null 2>&1; then
        printf 'python3'
    else
        return 1
    fi
}

# ---------------------------------------------------------------------------
# JSON field reader (PRD-FIX-156-FR04, B71-22)
#
# Prints the first of the dotted paths ($@, e.g. .tool_input.file_path) whose
# value in the JSON on stdin is neither null nor false, when that value is a
# string: jq's `(.a // .b // empty) | strings`. jq when present, else python3
# from PATH, so a jq-less host still gets its hint. Deliberately NOT
# _get_python_path: that honours a checkout-written cc03-python.txt, and parsing
# a payload must not run a program the checkout chose.
# With neither, or on malformed JSON, it prints nothing and returns 1; the
# caller keeps its allow / print-nothing exit. This bundle does not ship
# lib-trw.sh, whose _json_get is the same reader for the other hooks.
# ---------------------------------------------------------------------------

_read_json_field() {
    if command -v jq >/dev/null 2>&1; then
        _rjf_filter=""
        for _rjf_path in "$@"; do
            _rjf_filter="${_rjf_filter}${_rjf_filter:+ // }${_rjf_path}"
        done
        jq -r "(${_rjf_filter} // empty) | strings" 2>/dev/null
        return
    fi
    command -v python3 >/dev/null 2>&1 || return 1
    python3 -I -c '
import json, sys
try:
    node = json.load(sys.stdin)
except ValueError:
    sys.exit(1)
doc = node
for path in sys.argv[1:]:
    node = doc
    for key in path.lstrip(".").split("."):
        node = node.get(key) if isinstance(node, dict) else None
    if node is not None and node is not False:
        break
if isinstance(node, str):
    sys.stdout.write(node + "\n")
' "$@" 2>/dev/null
}

_worktree_pythonpath() {
    # Usage: _worktree_pythonpath <project_dir>
    # A linked worktree shares the main checkout's .venv, whose editable install
    # imports the MAIN checkout's source, so a hint hook started from a worktree
    # ran main's trw_mcp instead of the code under test. Prints a PYTHONPATH that
    # puts the worktree's own source trees first (each top-level <dir>/src, else a
    # <dir> holding a pyproject.toml); empty when <project_dir> is not a linked
    # worktree (its .git is a file) or carries none of them.
    [ -f "$1/.git" ] || return 0
    _wt_pp=""
    for _wt_dir in "$1"/*/; do
        _wt_dir=${_wt_dir%/}
        if [ -d "$_wt_dir/src" ]; then
            _wt_pp="${_wt_pp:+$_wt_pp:}$_wt_dir/src"
        elif [ -f "$_wt_dir/pyproject.toml" ]; then
            _wt_pp="${_wt_pp:+$_wt_pp:}$_wt_dir"
        fi
    done
    printf '%s' "$_wt_pp"
    return 0
}

_path_inside_repo() {
    # Usage: _path_inside_repo <file_path> <project_dir>
    # Returns 0 when the file's RESOLVED physical location is under <project_dir>'s, 1 when it is
    # outside. Pure sh (no interpreter start; ~ms), so a hook can skip a target no sidecar can know
    # (a scratch file under /tmp) before paying for Python. The file need not exist yet (a new file
    # resolves through its deepest existing directory); symlinks are followed both on the file and
    # on every directory, so a link cannot smuggle an outside file in or an inside one out. An
    # unresolvable project dir returns 0: when in doubt, keep hinting.
    _pir_root=$(cd "$2" 2>/dev/null && pwd -P) || return 0
    _pir_p=$1
    case "$_pir_p" in /*) ;; *) _pir_p="$_pir_root/$_pir_p" ;; esac  # a relative path is relative to the project
    _pir_n=0
    while [ -L "$_pir_p" ] && [ "$_pir_n" -lt 8 ]; do
        _pir_t=$(readlink "$_pir_p" 2>/dev/null) || break
        case "$_pir_t" in /*) _pir_p=$_pir_t ;; *) _pir_p="${_pir_p%/*}/$_pir_t" ;; esac
        _pir_n=$((_pir_n + 1))
    done
    _pir_tail=""
    _pir_d=$_pir_p
    while [ ! -d "$_pir_d" ]; do
        _pir_tail="/${_pir_d##*/}$_pir_tail"
        _pir_d=${_pir_d%/*}
        [ -n "$_pir_d" ] || _pir_d=/
    done
    _pir_real=$(cd "$_pir_d" 2>/dev/null && pwd -P) || return 0
    case "$_pir_real$_pir_tail/" in
        "$_pir_root"/*) return 0 ;;
    esac
    return 1
}

# ---------------------------------------------------------------------------
# Config field reader (POSIX sh, no jq required)
# ---------------------------------------------------------------------------

_read_trw_config_field() {
    # Usage: _read_trw_config_field <field_name> <default>
    _field="$1"
    _default="${2:-}"
    _config="$(_resolve_project_dir)/.trw/config.yaml"
    if [ ! -f "$_config" ]; then
        printf '%s' "$_default"
        return
    fi
    _val=$(grep -m1 "^${_field}:" "$_config" 2>/dev/null | sed 's/^[^:]*:[[:space:]]*//' | tr -d '"'"'" 2>/dev/null) || true
    if [ -n "$_val" ]; then
        printf '%s' "$_val"
    else
        printf '%s' "$_default"
    fi
}

# ---------------------------------------------------------------------------
# Nested YAML field reader (POSIX sh, no jq/yq required)
# ---------------------------------------------------------------------------

_read_trw_nested_config_field() {
    # Usage: _read_trw_nested_config_field <parent_key> <child_key> <default>
    _parent="$1"
    _child="$2"
    _default="${3:-}"
    _config="$(_resolve_project_dir)/.trw/config.yaml"
    if [ ! -f "$_config" ]; then
        printf '%s' "$_default"
        return
    fi
    _val=$(awk -v parent="${_parent}:" -v child="${_child}:" '
        /^[^ \t]/ { in_block = ($0 ~ "^" parent) }
        in_block && /^[ \t]/ {
            if ($0 ~ child) {
                sub(/^[^:]*:[[:space:]]*/, "")
                gsub(/["\x27]/, "")
                print
                exit
            }
        }
    ' "$_config" 2>/dev/null) || true
    if [ -n "$_val" ]; then
        printf '%s' "$_val"
    else
        printf '%s' "$_default"
    fi
}

# ---------------------------------------------------------------------------
# Shared opt-in gate (cc03_hook_enabled) — FR-6
# ---------------------------------------------------------------------------

_get_cc03_enabled() {
    # Returns 0 (true) if the shared cc03_hook_enabled gate is on, 1 otherwise.
    #
    # Precedence (matches Claude/Gemini libs + Python read_cc03_config):
    #   1. Top-level cc03_hook_enabled: true|false  (highest priority override)
    #   2. channels.cc03_hook_enabled: true|false   (canonical documented path)
    #   3. channels.cc03.enabled: true|false         (alternative nested path)
    _config="$(_resolve_project_dir)/.trw/config.yaml"

    _top=$(_read_trw_config_field "cc03_hook_enabled" "")
    if [ -n "$_top" ]; then
        case "$_top" in
            true|True|yes|1) return 0 ;;
            *) return 1 ;;
        esac
    fi

    _nested=$(_read_trw_nested_config_field "channels" "cc03_hook_enabled" "")
    if [ -n "$_nested" ]; then
        case "$_nested" in
            true|True|yes|1) return 0 ;;
            *) return 1 ;;
        esac
    fi

    if [ -f "$_config" ]; then
        _cc03_enabled=$(awk '
            /^channels:/ { in_channels=1; next }
            in_channels && /^[ \t]+cc03:/ { in_cc03=1; next }
            in_cc03 && /^[ \t]+enabled:/ {
                sub(/^[^:]*:[[:space:]]*/, "")
                gsub(/["\x27]/, "")
                print
                exit
            }
            /^[^ \t]/ && !/^channels:/ { in_channels=0; in_cc03=0 }
        ' "$_config" 2>/dev/null) || true
        if [ -n "$_cc03_enabled" ]; then
            case "$_cc03_enabled" in
                true|True|yes|1) return 0 ;;
                *) return 1 ;;
            esac
        fi
    fi

    return 1
}

# ---------------------------------------------------------------------------
# Symlink-safe atomic state write/read (PRD-SEC/RC8)
# ---------------------------------------------------------------------------
#
# Duplicated from hooks/lib-trw.sh rather than sourced: this hook deliberately
# does not source lib-trw.sh (see hooks/cursor/trw-before-shell.sh's
# _json_escape comment — a security-relevant shell must not pull code into its
# deciding shell from a second file). Advisory-only writers still need the
# same guarantee lib-trw.sh's `_trw_safe_write` gives its callers: a crafted
# checkout that ships a `.trw/context` state path (or `.trw/context` itself)
# as a symlink to an arbitrary file must not let a normal edit-hint run
# truncate or append to it, no race required.

_trw_ancestor_symlinked() {
    _tas_walk="$1"
    while [ -n "$_tas_walk" ] && [ "$_tas_walk" != "/" ] && [ "$_tas_walk" != "." ]; do
        [ -L "$_tas_walk" ] && return 0
        case "$_tas_walk" in
            */.trw | .trw) return 1 ;;
        esac
        _tas_next=$(dirname "$_tas_walk")
        [ "$_tas_next" = "$_tas_walk" ] && return 1
        _tas_walk="$_tas_next"
    done
    return 1
}

_trw_safe_write() {
    # Usage: printf '%s' "$content" | _trw_safe_write <dest>  (replace only;
    # lib-trw.sh's copy also appends)
    _tsw_dest="$1"
    _tsw_dir=$(dirname "$_tsw_dest")
    _trw_ancestor_symlinked "$_tsw_dir" && return 1
    [ -d "$_tsw_dir" ] || mkdir -p "$_tsw_dir" 2>/dev/null || return 1
    [ -L "$_tsw_dir" ] && return 1
    # A symlinked leaf would take `mv` into the directory it names.
    [ -L "$_tsw_dest" ] && return 1
    [ ! -e "$_tsw_dest" ] || [ -f "$_tsw_dest" ] || return 1
    # PID-suffixed temp name, not mktemp: mktemp is absent from some
    # minimal/restricted-PATH environments these hooks run in.
    _tsw_tmp="${_tsw_dest}.trw-safe-write.$$"
    rm -f "$_tsw_tmp" 2>/dev/null
    # noclobber: a temp name planted after the rm is refused, not opened.
    (set -C; cat > "$_tsw_tmp") 2>/dev/null || { rm -f "$_tsw_tmp" 2>/dev/null; return 1; }
    mv -f "$_tsw_tmp" "$_tsw_dest" 2>/dev/null && return 0
    rm -f "$_tsw_tmp" 2>/dev/null
    return 1
}

_trw_safe_read() {
    [ -L "$1" ] && return 1
    [ -f "$1" ] || return 1
    cat "$1" 2>/dev/null
}

# ---------------------------------------------------------------------------
# Session-scoped identical-hint dedup (PRD-CORE-301 cut 2)
# ---------------------------------------------------------------------------
#
# Mirrors the Claude Code lib's dedup: the per-file debounce above bounds HOW
# OFTEN a hint can fire; this dedups on CONTENT so a file edited repeatedly
# across a long session does not re-print an unchanged hint every time the
# debounce window lapses. A changed hint, or the first hint for a file,
# always fires.

_distill_hint_already_seen() {
    # Usage: _distill_hint_already_seen <project_root> <file_path> <hint_text>
    # Returns 0 (true, suppress as a duplicate) or 1 (false, new/changed).
    _dh_root="$1"
    _dh_file_path="$2"
    _dh_text="$3"
    _dh_dir="${_dh_root}/.trw/context/cur06-hint-seen"
    _dh_name=$(printf '%s' "$_dh_file_path" | tr '/' '_' | tr -cd 'a-zA-Z0-9_.-')
    _dh_ck=$(printf '%s' "$_dh_file_path" | cksum | cut -d' ' -f1)
    _dh_record="${_dh_dir}/${_dh_name}-${_dh_ck}.hash"
    _dh_hash=$(printf '%s' "$_dh_text" | cksum | cut -d' ' -f1)
    _dh_dup=1
    _dh_prev=$(_trw_safe_read "$_dh_record") || _dh_prev=""
    [ "$_dh_prev" = "$_dh_hash" ] && _dh_dup=0
    printf '%s' "$_dh_hash" | _trw_safe_write "$_dh_record" || true
    return $_dh_dup
}

# ---------------------------------------------------------------------------
# Safe-extension check (shared allowlist of safe-to-skip extensions) — FR-6
# ---------------------------------------------------------------------------

_is_safe_extension() {
    # Usage: _is_safe_extension <file_path>
    # Returns 0 (true) if the file extension is in the safe-skip allowlist.
    _fp="$1"
    _ext="${_fp##*.}"
    case ".$_ext" in
        .md|.txt|.rst|.lock|.log) return 0 ;;
    esac
    case "$(basename "$_fp")" in
        .gitignore) return 0 ;;
    esac
    return 1
}
