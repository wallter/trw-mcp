#!/bin/sh
# PostCompact hook — records that a compaction completed. Prints nothing.
#
# PRD-CORE-301 FR06: Claude Code documents no injection of PostCompact output,
# and a recorded transcript (six compactions) shows plain PostCompact stdout
# only as a status line in the /compact command echo, never as model context.
# Recovery context reaches the model through the SessionStart hook's compact
# branch (session-start.sh), which replays the same pre-compaction marker that
# pre-compact.sh writes. Printing here only cost user-visible noise.
# Fail-open: any error silently exits 0. Never blocks.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

log_hook_execution "PostCompact" "" "0"

exit 0
