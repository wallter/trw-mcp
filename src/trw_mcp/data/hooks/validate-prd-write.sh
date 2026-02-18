#!/bin/sh
# PRD-CORE-011-FR05: PreToolUse hook for write-scope enforcement.
# Ensures planning agents can only write to PRD files, planning run
# directories, and agent memory directories.
#
# Exit codes:
#   0 = allow the write
#   2 = deny the write (stderr explains why)
#
# Dependencies: jq (POSIX shell + jq only)

# Source shared utilities for get_task_root
_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || true

# Read task_root from config (defaults to "docs" if unavailable)
_task_root=$(get_task_root 2>/dev/null) || _task_root="docs"

# Read JSON input from stdin
input=$(cat)

# Fail-open on malformed JSON: if we cannot parse, allow the write
# rather than blocking all writes due to a hook error (NFR02).
file_path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || exit 0

# If no file_path extracted, allow (may be a non-file tool invocation)
if [ -z "$file_path" ]; then
  exit 0
fi

# Rule 1: Allow writes to PRD files (under task_root AARE-F directory)
case "$file_path" in
  */requirements-aare-f/prds/PRD-*.md)
    exit 0
    ;;
esac

# Rule 2: Allow writes under planning run directories
case "$file_path" in
  */requirements-aare-f/planning-runs/*)
    exit 0
    ;;
esac

# Rule 3: Allow writes under agent memory directories
case "$file_path" in
  */.claude/agent-memory/*)
    exit 0
    ;;
esac

# Rule 4: Allow writes under run directories (standard TRW runs)
case "$file_path" in
  */"$_task_root"/*/runs/*)
    exit 0
    ;;
esac

# Rule 5: Allow writes under .trw/ directory (learnings, context, etc.)
case "$file_path" in
  */.trw/*)
    exit 0
    ;;
esac

# Deny all other writes
echo "BLOCKED: Planning agents may only write to PRD files, planning run directories, and agent memory. Attempted: $file_path" >&2
exit 2
