#!/bin/sh
# PRD-FIX-140-FR01: PreToolUse DIAGNOSTIC for trw_deliver. It never blocks; the
# server deliver gate decides.
# Matcher: mcp__trw__trw_deliver
#
# This script used to re-derive its own delivery verdict from the project-global
# .trw/context/build-status.yaml (PRD-INFRA-038, PRD-FIX-077, PRD-CORE-214). That
# file carries no task type, no session identity and no changed-file evidence, so
# the two layers disagreed on the same run: on 2026-09-16 the server reported a
# docs-only research delivery READY while this hook exited 2 with "No build record
# exists yet". The authoritative decision now lives in exactly one place —
# trw_mcp/tools/_deliver_gate_mode.py and the dispatch around it — and the two
# rules that lived only here (an unpinned session's recorded build FAILURE, and a
# run whose latest build check failed) moved there with it.
#
# TRW_BUILD_FRESHNESS_SECS is RETIRED: wall-clock receipt age is not read by any
# layer any more. The server uses edit-bound and content-bound staleness instead.
#
# Exit 0 always. Output is evidence, never a verdict.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

_payload=$(cat) || exit 0
# jq only -- no shell JSON parser fallback (PRD-FIX-149 FR06 binding decision).
_tool_name=$(_json_str_field "$_payload" tool_name) || true

case "$_tool_name" in
  *trw_deliver*) ;;
  *)
    # Without jq, tool_name could not be read, so a genuine trw_deliver call is
    # indistinguishable from any other tool here. This hook never blocks either
    # way; log ONE diagnostic disclosing the gap instead of silently guessing
    # "not a deliver call" (PRD-FIX-149 FR06).
    command -v jq >/dev/null 2>&1 || log_hook_execution "PreToolUse:deliver-gate" "unknown" "0" "jq_unavailable=1"
    exit 0
    ;;
esac

_project_root="$(get_repo_root)" || exit 0
_build_status="$_project_root/.trw/context/build-status.yaml"
_state_file="$_project_root/.trw/context/ceremony-state.json"
_evidence=""

if [ -r "$_build_status" ]; then
  _passed=$(grep '^tests_passed:' "$_build_status" 2>/dev/null | head -1 | sed 's/^tests_passed:[[:space:]]*//' | tr -d "'\"") || true
  _scope=$(grep '^scope:' "$_build_status" 2>/dev/null | head -1 | sed 's/^scope:[[:space:]]*//' | tr -d "'\"" | cut -c1-80) || true
  _evidence="build-status.yaml tests_passed=${_passed:-unset} scope=${_scope:-unset}"
elif [ -e "$_build_status" ]; then
  _evidence="build-status.yaml present but unreadable"
else
  _evidence="no build-status.yaml"
fi

if [ -r "$_state_file" ] && command -v jq >/dev/null 2>&1; then
  _state_result=$(jq -r '.build_check_result // "unset"' "$_state_file" 2>/dev/null || printf 'unreadable')
  _state_ts=$(jq -r '.last_build_check_ts // "unset"' "$_state_file" 2>/dev/null || printf 'unreadable')
  _evidence="$_evidence; ceremony-state build_check_result=$_state_result at $_state_ts"
fi

printf 'DELIVER-GATE (diagnostic): %s\n' "$_evidence"
printf 'This hook never blocks; the server deliver gate decides from the run task type, this session changed-file evidence and the latest build result.\n'
log_hook_execution "PreToolUse:deliver-gate" "$_tool_name" "0:diagnostic"
exit 0
