#!/bin/sh
# PRD-INFRA-038-FR05: SubagentStop hook — telemetry event logging.
# Emits structured JSONL to .trw/logs/subagent-events.jsonl for lifecycle tracking.
# Fail-open: any error silently exits 0.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

_project_root="$(get_repo_root)" || exit 0
_log_dir="$_project_root/.trw/logs"
[ -d "$_log_dir" ] || mkdir -p "$_log_dir" 2>/dev/null || exit 0

# Read stdin payload
_payload=$(cat) || exit 0
# PRD-FIX-154 FR03: _json_get reads, _json_object writes -- jq or python3,
# never a shell parser, and never the "unknown" placeholder a jq-less host
# used to write even when python3 could have read the real value.
_agent_type=$(printf '%s' "$_payload" | _json_get --strings --default "unknown" .agent_type .subagent_type) \
  || _agent_type="unknown"

_ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)" || _ts="unknown"

# Write telemetry event (key: "ts" matches lib-trw.sh append_event convention)
_log_file="$_log_dir/subagent-events.jsonl"
_json_object --str ts "$_ts" --str event "subagent_stop" --str agent_type "$_agent_type" \
  | _trw_safe_write "$_log_file" append 2>/dev/null

log_hook_execution "SubagentStop" "$_agent_type" "0"
exit 0
