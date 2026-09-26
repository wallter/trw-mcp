#!/bin/sh
# CC-03/CC-04 shared helper library for Claude Code distill hint hooks.
#
# PRD-DIST-2405 Phase C.
#
# POSIX sh only — no bashisms. Source this file at the top of hook scripts:
#   . "$_hook_dir/lib-distill-hint.sh" 2>/dev/null || exit 0
#
# Provides:
#   _get_python_path()           — resolves venv Python path
#   _read_trw_config_field()     — reads a field from .trw/config.yaml
#   _get_cc03_enabled()          — checks if CC-03 hook is enabled (opt-in)
#   _is_safe_extension()         — returns 0 if extension should be skipped
#   _write_distill_snapshot_bg() — background CC-01 snapshot write trigger
#   _format_t0_beacon()          — outputs T0 presence beacon

# ---------------------------------------------------------------------------
# Python path resolution
# ---------------------------------------------------------------------------

_get_python_path() {
    # Reads .trw/channels/cc03-python.txt first (set at init-project time)
    _python_path_file="${TRW_PROJECT_DIR:-$(pwd)}/.trw/channels/cc03-python.txt"
    if [ -f "$_python_path_file" ]; then
        _py=$(cat "$_python_path_file" 2>/dev/null)
        if [ -n "$_py" ] && [ -x "$_py" ]; then
            printf '%s' "$_py"
            return 0
        fi
    fi
    # Fall back to python3 in PATH
    if command -v python3 >/dev/null 2>&1; then
        printf 'python3'
        return 0
    fi
    return 1
}

# ---------------------------------------------------------------------------
# Config field reader (POSIX sh, no jq required)
# ---------------------------------------------------------------------------

_read_trw_config_field() {
    # Usage: _read_trw_config_field <field_name> <default>
    # Reads a top-level scalar YAML field from .trw/config.yaml using grep.
    _field="$1"
    _default="${2:-}"
    _config="${TRW_PROJECT_DIR:-$(pwd)}/.trw/config.yaml"
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
    # Reads a scalar value nested one level under a YAML block key.
    # e.g. _read_trw_nested_config_field "channels" "cc03_hook_enabled" "false"
    # matches:
    #   channels:
    #     cc03_hook_enabled: true
    # Uses awk: enters parent block on "^parent_key:", exits on next top-level key.
    # Dependency-free (no jq/yq): PRD-FIX-154 removed a dead "optional jq" branch
    # here that always fell through to this same awk regardless (jq cannot parse
    # YAML), so it never had an effect.
    _parent="$1"
    _child="$2"
    _default="${3:-}"
    _config="${TRW_PROJECT_DIR:-$(pwd)}/.trw/config.yaml"
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
# CC-03 opt-in gate (FR09)
# ---------------------------------------------------------------------------

_get_cc03_enabled() {
    # Returns 0 (true) if CC-03 hook is enabled, 1 (false) otherwise.
    #
    # Precedence (matches Python _hook_helpers.py + documented config path):
    #   1. Top-level cc03_hook_enabled: true|false  (highest priority override)
    #   2. channels.cc03_hook_enabled: true|false   (canonical documented path)
    #   3. channels.cc03.enabled: true|false         (alternative nested path)
    #
    # Operators enabling via the documented path (.trw/config.yaml
    # channels.cc03_hook_enabled=true) are correctly handled here.
    _config="${TRW_PROJECT_DIR:-$(pwd)}/.trw/config.yaml"

    # Check 1: top-level cc03_hook_enabled (overrides all)
    _top=$(_read_trw_config_field "cc03_hook_enabled" "")
    if [ -n "$_top" ]; then
        case "$_top" in
            true|True|yes|1) return 0 ;;
            *) return 1 ;;
        esac
    fi

    # Check 2: channels.cc03_hook_enabled (canonical documented path)
    _nested=$(_read_trw_nested_config_field "channels" "cc03_hook_enabled" "")
    if [ -n "$_nested" ]; then
        case "$_nested" in
            true|True|yes|1) return 0 ;;
            *) return 1 ;;
        esac
    fi

    # Check 3: channels.cc03.enabled (alternative nested path)
    # Handled by checking for a "cc03:" sub-block under "channels:" — use awk
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
# Safe-extension check (P0-10 fix — allowlist of safe-to-skip extensions)
# ---------------------------------------------------------------------------

_is_safe_extension() {
    # Usage: _is_safe_extension <file_path>
    # Returns 0 (true) if the file extension is in the safe-skip allowlist.
    # Returns 1 (false) if the file should receive a hint.
    _fp="$1"
    _ext="${_fp##*.}"
    # Build the allowlist: .md .txt .rst .lock .log .gitignore
    case ".$_ext" in
        .md|.txt|.rst|.lock|.log) return 0 ;;
    esac
    # Check for .gitignore (no extension — basename check)
    case "$(basename "$_fp")" in
        .gitignore) return 0 ;;
    esac
    return 1
}

# ---------------------------------------------------------------------------
# T0 beacon formatter
# ---------------------------------------------------------------------------

_format_t0_beacon() {
    printf '[TRW] Distill intelligence available — run trw_code(mode="hint") for details.'
}

# ---------------------------------------------------------------------------
# Session-scoped identical-hint dedup (PRD-CORE-301 cut 2)
# ---------------------------------------------------------------------------
#
# The per-file debounce above bounds HOW OFTEN a hint can fire; it says
# nothing about whether two hints more than the debounce window apart carry
# the SAME text. A file edited repeatedly across a long session re-prints an
# unchanged risk report every time the debounce window lapses, paying its
# full token cost again for zero new information. This dedups on CONTENT: a
# hint byte-identical to the last one recorded for this file THIS SESSION is
# suppressed even outside the debounce window; a changed hint always fires,
# and the very first hint for a file always fires (nothing recorded yet).

_distill_hint_already_seen() {
    # Usage: _distill_hint_already_seen <project_root> <file_path> <hint_text>
    # Returns 0 (true, suppress as a duplicate) or 1 (false, new/changed —
    # caller should print it). Records the new hash as a side effect either
    # way, so the state always reflects the most recently COMPUTED hint, not
    # only the ones that were printed.
    _dh_root="$1"
    _dh_file_path="$2"
    _dh_text="$3"
    _dh_dir="${_dh_root}/.trw/context/cc03-hint-seen"
    # Same sanitize-plus-checksum naming as the debounce keys above: the
    # sanitizer alone collapses distinct paths with the same non-ASCII-free
    # characters onto the same file.
    _dh_name=$(printf '%s' "$_dh_file_path" | tr '/' '_' | tr -cd 'a-zA-Z0-9_.-')
    _dh_ck=$(printf '%s' "$_dh_file_path" | cksum | cut -d' ' -f1)
    _dh_record="${_dh_dir}/${_dh_name}-${_dh_ck}.hash"
    _dh_hash=$(printf '%s' "$_dh_text" | cksum | cut -d' ' -f1)
    _dh_dup=1
    # PRD-SEC/RC8: _trw_safe_read/_trw_safe_write treat a symlinked record as
    # absent and never write through one -- a crafted checkout symlinking
    # this per-file dedup record to an arbitrary path must not be able to
    # overwrite it via a normal edit hint. Defined below when this lib is
    # sourced standalone; when the real hook also sources lib-trw.sh, that
    # (identical) definition simply wins.
    _dh_prev=$(_trw_safe_read "$_dh_record") || _dh_prev=""
    [ "$_dh_prev" = "$_dh_hash" ] && _dh_dup=0
    printf '%s' "$_dh_hash" | _trw_safe_write "$_dh_record" || true
    return $_dh_dup
}

# ---------------------------------------------------------------------------
# Symlink-safe atomic state write/read (PRD-SEC/RC8)
# ---------------------------------------------------------------------------
#
# Duplicated from hooks/lib-trw.sh so this lib works standalone (its own
# tests source it alone) and so the same guarantee holds even if a future
# caller stops co-sourcing lib-trw.sh. A crafted checkout that ships a
# `.trw/context` state path (or `.trw/context` itself) as a symlink to an
# arbitrary file must not let a normal edit-hint run truncate or append to
# it, no race required.

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
# Background CC-01 snapshot write
# ---------------------------------------------------------------------------

_write_distill_snapshot_bg() {
    # Triggers a background CC-01 snapshot write via Python.
    # Fails silently — never blocks the hook caller.
    _py=$(_get_python_path 2>/dev/null) || return 0
    _repo="${TRW_PROJECT_DIR:-$(pwd)}"
    (
        PYTHONDONTWRITEBYTECODE=1 PYTHONOPTIMIZE=1 \
        TRW_CC01_REPO_ROOT="$_repo" \
        "$_py" -c '
# Single-quoted, repo root via the environment. $_repo is not model-controlled,
# but git_hooks/trw-post-commit.sh states the invariant for every hook in this
# tree: "a repo path containing quotes or newlines must not be able to inject
# code into the -c program". A checkout under a directory with an apostrophe was
# enough to break this one.
import os
from pathlib import Path
from trw_mcp.channels.claude_code import write_distill_snapshot
write_distill_snapshot(repo_root=Path(os.environ["TRW_CC01_REPO_ROOT"]), tier="T2")
' >/dev/null 2>&1
    ) &
}
