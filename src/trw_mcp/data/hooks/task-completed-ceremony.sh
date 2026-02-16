#!/bin/sh
# PRD-INFRA-004-FR01: TaskCompleted hook — ceremony enforcement gate.
# Blocks (exit 2) if events logged but ceremony incomplete, max 2 blocks.
# After 2 blocks, warns but allows. Fail-open on errors.
set -e
_trw_intentional_exit=""
trap '[ -n "$_trw_intentional_exit" ] || exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

_project_root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
_block_file="$_project_root/.trw/context/task_ceremony_block_count"
_context_dir="$_project_root/.trw/context"

# Check ceremony status — returns missing steps or empty if complete.
# Returns non-zero if no active run or event count < 3.
_missing=$(check_ceremony_status) || exit 0

# If ceremony complete or too few events, reset counter and allow
if [ -z "$_missing" ]; then
  rm -f "$_block_file" 2>/dev/null || true
  exit 0
fi

# Read and manage block counter (same pattern as stop-ceremony.sh)
[ -d "$_context_dir" ] || mkdir -p "$_context_dir" 2>/dev/null || exit 0
_blocks=0
if [ -f "$_block_file" ]; then
  _blocks=$(tr -d '[:space:]' < "$_block_file" 2>/dev/null) || true
fi
_blocks=$((${_blocks:-0} + 0)) 2>/dev/null || _blocks=0

if [ "$_blocks" -ge 2 ]; then
  echo "TRW WARNING: Ceremony incomplete. Allowing after 2 blocks." >&2
  rm -f "$_block_file" 2>/dev/null || true
  log_hook_execution "TaskCompleted" "" "0"
  exit 0
fi

# Block: increment counter and exit 2
_blocks=$((_blocks + 1))
printf '%s' "$_blocks" > "$_block_file" 2>/dev/null || true
echo "$_missing (Block $_blocks/2)" >&2

log_hook_execution "TaskCompleted" "" "2"
_trw_intentional_exit=1
exit 2
