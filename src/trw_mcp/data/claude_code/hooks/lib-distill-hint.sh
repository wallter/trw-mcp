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
    # Dependency-free (no jq/yq); optional jq used if available.
    _parent="$1"
    _child="$2"
    _default="${3:-}"
    _config="${TRW_PROJECT_DIR:-$(pwd)}/.trw/config.yaml"
    if [ ! -f "$_config" ]; then
        printf '%s' "$_default"
        return
    fi
    # Optional: use jq when available for robustness (handles multi-line + comments)
    if command -v jq >/dev/null 2>&1; then
        # jq can't parse YAML, so we still fall through to awk
        :
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
    printf '[TRW] Distill intelligence available — run trw_before_edit_hint for details.'
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
        "$_py" -c "
from trw_mcp.channels.claude_code import write_distill_snapshot
from pathlib import Path
write_distill_snapshot(repo_root=Path('$_repo'), tier='T2')
" >/dev/null 2>&1
    ) &
}
