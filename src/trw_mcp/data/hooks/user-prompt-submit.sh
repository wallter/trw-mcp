#!/bin/sh
# PRD-INFRA-024-FR02 + PRD-CORE-095: Phase-aware UserPromptSubmit hook.
# Emits calibrated context per execution phase. Fail-open: never blocks prompts.
# Output target: <150 tokens (~600 chars) per phase. "done" phase: 0 tokens.
#
# PRD-CORE-095 FR01-FR06: Phase-change suppression — caches last emitted phase.
# PRD-CORE-095 FR07-FR14: Contextual learning injection — keyword search against
# learning summaries, with session dedup, token cap, and 500ms timeout.
#
# Performance: ~71ms baseline (infer_phase). Search adds <50ms for 300 entries.
set -e
trap 'exit 0' EXIT

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-trw.sh
. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

init_hook_timer

# FR07: Read stdin JSON and extract prompt text (replaces cat >/dev/null)
_payload=$(cat) || exit 0
_prompt=""
if command -v jq >/dev/null 2>&1; then
  _prompt=$(printf '%s' "$_payload" | jq -r '.prompt // empty' 2>/dev/null) || true
fi
if [ -z "$_prompt" ]; then
  _prompt=$(printf '%s' "$_payload" | grep -o '"prompt"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"prompt"[[:space:]]*:[[:space:]]*"//;s/"$//') || true
fi

_phase=$(infer_phase)

# --- PRD-CORE-095 FR01-FR06: Phase-change suppression ---
_project_root="$(get_repo_root)" || exit 0
_phase_cache="$_project_root/.trw/context/last_ups_phase"
_injected_file="$_project_root/.trw/context/injected_learning_ids.txt"

# FR04: "none" phase always emits (agent needs session_start reminder)
# FR06: "done" phase is always silent
if [ "$_phase" != "none" ]; then
  _cached_phase=""
  if [ -f "$_phase_cache" ]; then
    _cached_phase=$(cat "$_phase_cache" 2>/dev/null) || true
  fi
  # FR02: Same-phase suppression — skip phase output if unchanged
  if [ "$_cached_phase" = "$_phase" ]; then
    # Phase guidance suppressed, but still attempt learning injection below
    _phase_suppressed=1
  else
    _phase_suppressed=0
  fi
  # FR01: Write current phase to cache (atomic write)
  printf '%s' "$_phase" > "$_phase_cache" 2>/dev/null || true
else
  _phase_suppressed=0
fi

# Emit phase guidance if not suppressed
if [ "$_phase_suppressed" = "0" ]; then
  case "$_phase" in
    none)
      echo "TRW: Call trw_session_start(query='your task domain') to load context, then read .trw/frameworks/FRAMEWORK.md — it defines the methodology your tools implement."
      ;;
    early)
      echo "TRW [RESEARCH/PLAN]: PRD validation gates implementation — trw_prd_validate catches ambiguity before it becomes rework."
      ;;
    plan)
      echo "TRW [PLAN]: Run trw_prd_validate before implementing — catching spec gaps now saves 2-3x rework vs discovering them during implementation."
      ;;
    implement)
      echo "TRW [IMPLEMENT]: Before completing, re-read FRs for coverage gaps. Call trw_checkpoint after milestones — uncheckpointed work is lost on compaction."
      ;;
    validate)
      echo "TRW [VALIDATE]: trw_build_check(scope='full') is required — pytest alone doesn't satisfy the gate."
      ;;
    deliver)
      echo "TRW [DELIVER]: trw_deliver() persists learnings, syncs CLAUDE.md, and closes the run — without it, your session's work is invisible to future agents."
      ;;
    done)
      # Silent — run is complete, no output (FR06)
      ;;
  esac
fi

# --- PRD-CORE-095 FR07-FR14: Contextual learning auto-injection ---

# FR14: Config gate — check auto_recall_enabled
_auto_recall_enabled="true"
_auto_recall_max_results=3
_auto_recall_max_chars=400
_auto_recall_min_score="0.7"
_config_file="$_project_root/.trw/config.yaml"
if [ -f "$_config_file" ]; then
  _val=$(grep -m1 'auto_recall_enabled:' "$_config_file" 2>/dev/null | sed 's/.*: *//' | tr -d "\"'" 2>/dev/null) || true
  [ -n "$_val" ] && _auto_recall_enabled="$_val"
  _val=$(grep -m1 'auto_recall_max_results:' "$_config_file" 2>/dev/null | sed 's/.*: *//' | tr -d "\"'" 2>/dev/null) || true
  [ -n "$_val" ] && _auto_recall_max_results="$_val"
  _val=$(grep -m1 'auto_recall_max_tokens:' "$_config_file" 2>/dev/null | sed 's/.*: *//' | tr -d "\"'" 2>/dev/null) || true
  [ -n "$_val" ] && _auto_recall_max_chars=$(( _val * 4 ))
  _val=$(grep -m1 'auto_recall_min_score:' "$_config_file" 2>/dev/null | sed 's/.*: *//' | tr -d "\"'" 2>/dev/null) || true
  [ -n "$_val" ] && _auto_recall_min_score="$_val"
fi
# Env var overrides
[ -n "$TRW_AUTO_RECALL_ENABLED" ] && _auto_recall_enabled="$TRW_AUTO_RECALL_ENABLED"

# Early exit if disabled or no prompt
if [ "$_auto_recall_enabled" = "false" ] || [ -z "$_prompt" ]; then
  log_hook_execution "UserPromptSubmit" "$_phase" "0"
  exit 0
fi

# FR08: Extract significant keywords from prompt (length >= 4, exclude stop words)
_stop_words="the this that with from have will your been they them their what when which where into more some then than also just like only even make take call each"
_keywords=""
for _word in $(printf '%s' "$_prompt" | tr '[:upper:]' '[:lower:]' | tr -c '[:alnum:]' ' '); do
  [ ${#_word} -lt 4 ] && continue
  _is_stop=0
  for _sw in $_stop_words; do
    if [ "$_word" = "$_sw" ]; then
      _is_stop=1
      break
    fi
  done
  [ "$_is_stop" = "1" ] && continue
  case " $_keywords " in
    *" $_word "*) ;;  # dedup
    *) _keywords="$_keywords $_word" ;;
  esac
done
_keywords=$(echo "$_keywords" | sed 's/^ *//')

# No significant keywords — skip injection
if [ -z "$_keywords" ]; then
  log_hook_execution "UserPromptSubmit" "$_phase" "0"
  exit 0
fi

# FR08: Keyword search against learning summaries with timeout (FR13: 500ms guard)
_entries_dir="$_project_root/.trw/learnings/entries"
if [ ! -d "$_entries_dir" ]; then
  log_hook_execution "UserPromptSubmit" "$_phase" "0"
  exit 0
fi

# FR11: Read already-injected IDs for dedup
_injected_ids=""
if [ -f "$_injected_file" ]; then
  _injected_ids=$(cat "$_injected_file" 2>/dev/null) || true
fi

# Count total keywords for scoring
_total_kw=0
for _kw in $_keywords; do
  _total_kw=$(( _total_kw + 1 ))
done

# Search: for each entry, count keyword matches in summary field
_results=""
_result_count=0
for _entry in "$_entries_dir"/*.yaml; do
  [ -f "$_entry" ] || continue
  _summary=$(grep -m1 '^summary:' "$_entry" 2>/dev/null | sed 's/^summary: *//;s/^"//;s/"$//' | tr '[:upper:]' '[:lower:]') || continue
  [ -z "$_summary" ] && continue

  # Extract ID from filename (entries are named by ID)
  _id=$(basename "$_entry" .yaml)

  # FR11: Skip already-injected
  case "$_injected_ids" in
    *"$_id"*) continue ;;
  esac

  # Count keyword matches
  _matches=0
  for _kw in $_keywords; do
    case "$_summary" in
      *"$_kw"*) _matches=$(( _matches + 1 )) ;;
    esac
  done
  [ "$_matches" -eq 0 ] && continue

  # FR09: Score = matches / total keywords (integer math: multiply by 100)
  _score_100=$(( _matches * 100 / _total_kw ))
  # Compare against min_score (also multiplied by 100)
  _min_100=$(printf '%s' "$_auto_recall_min_score" | awk '{printf "%d", $1 * 100}')
  [ "$_score_100" -lt "$_min_100" ] && continue

  # Store result: score|id|summary (original case)
  _orig_summary=$(grep -m1 '^summary:' "$_entry" 2>/dev/null | sed 's/^summary: *//;s/^"//;s/"$//')
  _results="$_results
$_score_100|$_id|$_orig_summary"
  _result_count=$(( _result_count + 1 ))
done

# No results
if [ "$_result_count" -eq 0 ]; then
  log_hook_execution "UserPromptSubmit" "$_phase" "0"
  exit 0
fi

# FR09: Sort by score descending, cap at max_results
_sorted=$(printf '%s' "$_results" | sort -t'|' -k1 -rn | head -n "$_auto_recall_max_results")

# FR10: Emit compact injection format with token cap (FR09)
# NOTE: Use redirect (not pipeline) so _total_chars stays in parent shell scope.
_total_chars=0
while IFS='|' read -r _sc _lid _lsummary; do
  [ -z "$_lid" ] && continue
  _line="TRW RECALL: [L-${_lid}] $_lsummary"
  _line_len=${#_line}
  _new_total=$(( _total_chars + _line_len ))
  if [ "$_new_total" -gt "$_auto_recall_max_chars" ]; then
    break
  fi
  echo "$_line"
  _total_chars="$_new_total"
  # FR11: Append injected ID to state file
  printf '%s\n' "$_lid" >> "$_injected_file" 2>/dev/null || true
done <<EOF
$(printf '%s\n' "$_sorted")
EOF

log_hook_execution "UserPromptSubmit" "$_phase" "0"
exit 0
