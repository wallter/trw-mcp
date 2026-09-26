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
#   _get_python_path()           — resolves venv Python path
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
    # Reads .trw/channels/cc03-python.txt first (set at init-project time;
    # shared with CC-03 so the venv path is resolved once per repo).
    _python_path_file="$(_resolve_project_dir)/.trw/channels/cc03-python.txt"
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
