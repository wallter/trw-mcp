#!/bin/sh
# PRD-INFRA-002-FR01/FR02/FR03/FR04: Unified SessionStart hook.
# Dispatches on $SOURCE (startup|resume|compact|clear) from stdin JSON.
# Fail-open: any error silently exits 0.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

# Read stdin payload to determine source
_payload=$(cat) || exit 0
_source=""
if command -v jq >/dev/null 2>&1; then
  _source=$(printf '%s' "$_payload" | jq -r '.source // empty' 2>/dev/null) || true
fi
# Fallback: extract source via grep
if [ -z "$_source" ]; then
  _source=$(printf '%s' "$_payload" | grep -o '"source"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"source"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
fi

_project_root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0

# --- Common protocol ---
_protocol_file="$_project_root/.trw/context/behavioral_protocol.yaml"
_emit_protocol() {
  if [ -f "$_protocol_file" ]; then
    echo "TRW BEHAVIORAL PROTOCOL:"
    grep '^ *-' "$_protocol_file" | sed 's/^ *- *//;s/^"//;s/"$//'
  else
    echo "TRW BEHAVIORAL PROTOCOL: Execute trw_session_start() to load learnings. Use trw_checkpoint during work. Execute trw_deliver() at completion."
  fi
}

case "$_source" in
  startup)
    # FR01: Full startup protocol
    _emit_protocol
    echo "SESSION TYPE: Fresh startup"
    echo "ACTION: ALWAYS call trw_session_start() or trw_recall('*', min_impact=0.7) — NEVER skip this step"
    echo "ACTION: ALWAYS read .trw/frameworks/FRAMEWORK.md"
    echo "ACTION: ALWAYS execute trw_init for new tasks"
    ;;

  resume)
    # FR02: Resume protocol
    _emit_protocol
    echo "SESSION TYPE: Resumed session"
    echo "ACTION: ALWAYS call trw_status() to check active run state"
    echo "ACTION: ALWAYS call trw_recall('*', min_impact=0.7) for recent learnings"
    echo "ACTION: ALWAYS read .trw/frameworks/FRAMEWORK.md"
    ;;

  compact)
    # FR03: Compaction recovery protocol
    echo "NOTICE: Context was compacted — prior conversation state was compressed."
    _emit_protocol
    echo "SESSION TYPE: Post-compaction recovery"
    echo "ACTION: ALWAYS re-read .trw/frameworks/FRAMEWORK.md (lost during compaction)"
    echo "ACTION: ALWAYS call trw_recall('*', min_impact=0.7) — NEVER skip this step"
    echo "ACTION: ALWAYS call trw_status() to recover active run state"
    # Recover pre-compaction state if available
    _state_file="$_project_root/.trw/context/pre_compact_state.json"
    if [ -f "$_state_file" ] && command -v jq >/dev/null 2>&1; then
      _run_path=$(jq -r '.run_path // empty' "$_state_file" 2>/dev/null) || true
      _phase=$(jq -r '.phase // empty' "$_state_file" 2>/dev/null) || true
      [ -n "$_run_path" ] && echo "RECOVERED: Active run at $_run_path"
      [ -n "$_phase" ] && echo "RECOVERED: Phase was $_phase"
    fi
    ;;

  clear)
    # FR04: Minimal clear protocol
    _emit_protocol
    echo "SESSION TYPE: Cleared session"
    echo "ACTION: ALWAYS call trw_recall('*', min_impact=0.7)"
    ;;

  *)
    # Fallback for unknown source (including empty)
    _emit_protocol
    ;;
esac

log_hook_execution "SessionStart" "$_source" "0"

exit 0
