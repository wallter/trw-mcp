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
elif command -v python3 >/dev/null 2>&1; then
  _prompt=$(
    printf '%s' "$_payload" | python3 -c '
import json
import sys

try:
    payload = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)

prompt = payload.get("prompt", "") if isinstance(payload, dict) else ""
if isinstance(prompt, str):
    sys.stdout.write(prompt)
'
  ) || true
fi

_phase=$(infer_phase)
_project_root="$(get_repo_root)" || exit 0

# FR07: malformed or missing prompt input must stay fully silent.
if [ -z "$_prompt" ]; then
  log_hook_execution "UserPromptSubmit" "$_phase" "skipped"
  exit 0
fi

# --- PRD-CORE-095 FR01-FR06: Phase-change suppression ---
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
_auto_recall_max_tokens=100
_auto_recall_min_score="0.7"
_config_file="$_project_root/.trw/config.yaml"
if [ -f "$_config_file" ]; then
  _val=$(grep -m1 'auto_recall_enabled:' "$_config_file" 2>/dev/null | sed 's/.*: *//' | tr -d "\"'" 2>/dev/null) || true
  [ -n "$_val" ] && _auto_recall_enabled="$_val"
  _val=$(grep -m1 'auto_recall_max_results:' "$_config_file" 2>/dev/null | sed 's/.*: *//' | tr -d "\"'" 2>/dev/null) || true
  [ -n "$_val" ] && _auto_recall_max_results="$_val"
  _val=$(grep -m1 'auto_recall_max_tokens:' "$_config_file" 2>/dev/null | sed 's/.*: *//' | tr -d "\"'" 2>/dev/null) || true
  [ -n "$_val" ] && _auto_recall_max_tokens="$_val"
  _val=$(grep -m1 'auto_recall_min_score:' "$_config_file" 2>/dev/null | sed 's/.*: *//' | tr -d "\"'" 2>/dev/null) || true
  [ -n "$_val" ] && _auto_recall_min_score="$_val"
fi
# Env var overrides
[ -n "$TRW_AUTO_RECALL_ENABLED" ] && _auto_recall_enabled="$TRW_AUTO_RECALL_ENABLED"
[ -n "$TRW_AUTO_RECALL_MAX_RESULTS" ] && _auto_recall_max_results="$TRW_AUTO_RECALL_MAX_RESULTS"
[ -n "$TRW_AUTO_RECALL_MAX_TOKENS" ] && _auto_recall_max_tokens="$TRW_AUTO_RECALL_MAX_TOKENS"
[ -n "$TRW_AUTO_RECALL_MIN_SCORE" ] && _auto_recall_min_score="$TRW_AUTO_RECALL_MIN_SCORE"

# Early exit if disabled
if [ "$_auto_recall_enabled" = "false" ]; then
  log_hook_execution "UserPromptSubmit" "$_phase" "skipped"
  exit 0
fi

# FR06: done phase must be fully silent, including learning injection
if [ "$_phase" = "done" ]; then
  log_hook_execution "UserPromptSubmit" "$_phase" "silent"
  exit 0
fi

# FR02: Same-phase suppression must be fully silent, including learning injection.
if [ "$_phase_suppressed" = "1" ]; then
  log_hook_execution "UserPromptSubmit" "$_phase" "cached"
  exit 0
fi

# FR08: Keyword search against learning summaries with timeout (FR13: 500ms guard)
_entries_dir="$_project_root/.trw/learnings/entries"
if [ ! -d "$_entries_dir" ]; then
  log_hook_execution "UserPromptSubmit" "$_phase" "skipped"
  exit 0
fi

if [ "$_phase_suppressed" = "1" ]; then
  _phase_status="cached"
else
  _phase_status="emitted"
fi

_recall_output=$(
  python3 - "$_entries_dir" "$_prompt" "$_injected_file" "$_auto_recall_max_results" "$_auto_recall_max_tokens" "$_auto_recall_min_score" <<'PY'
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

TIMEOUT_NS = 500_000_000
MAX_KEYWORDS = 16
STOP_WORDS = {
    "also",
    "been",
    "call",
    "each",
    "even",
    "from",
    "have",
    "into",
    "just",
    "like",
    "make",
    "more",
    "only",
    "some",
    "take",
    "than",
    "that",
    "their",
    "them",
    "then",
    "they",
    "this",
    "what",
    "when",
    "where",
    "which",
    "will",
    "with",
    "your",
}


def _read_yaml_field(path: Path, field_name: str) -> str:
    prefix = f"{field_name}:"
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.startswith(prefix):
                continue
            value = line.split(":", 1)[1].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            return value
    except OSError:
        return ""
    return ""


entries_dir = Path(sys.argv[1])
prompt = sys.argv[2]
injected_file = Path(sys.argv[3])
max_results = max(0, int(sys.argv[4]))
max_tokens = max(0, int(sys.argv[5]))
min_score = float(sys.argv[6])
max_chars = max_tokens * 4
deadline_ns = time.monotonic_ns() + TIMEOUT_NS

keywords: list[str] = []
for word in re.findall(r"[A-Za-z0-9]+", prompt.lower()):
    if len(word) < 4 or word in STOP_WORDS or word in keywords:
        continue
    if len(keywords) >= MAX_KEYWORDS:
        break
    keywords.append(word)

if not keywords or not entries_dir.is_dir():
    raise SystemExit(0)

injected_ids: set[str] = set()
if injected_file.is_file():
    injected_ids = {
        line.strip()
        for line in injected_file.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    }

results: list[tuple[float, str, str, str]] = []
for entry in sorted(entries_dir.glob("*.yaml")):
    if time.monotonic_ns() >= deadline_ns:
        raise SystemExit(0)

    if _read_yaml_field(entry, "status").lower() != "active":
        continue

    entry_id = _read_yaml_field(entry, "id") or entry.stem
    if entry_id in injected_ids:
        continue

    summary = _read_yaml_field(entry, "summary")
    if not summary:
        continue

    lower_summary = summary.lower()
    matches = sum(1 for keyword in keywords if keyword in lower_summary)
    if matches == 0:
        continue

    score = matches / len(keywords)
    if score < min_score:
        continue
    display_id = entry_id if entry_id.startswith("L-") else f"L-{entry_id}"
    results.append((score, entry_id, display_id, summary))

if time.monotonic_ns() >= deadline_ns:
    raise SystemExit(0)

results.sort(key=lambda item: (-item[0], item[1]))
selected = results[:max_results]
if not selected:
    raise SystemExit(0)

lines: list[str] = []
emitted_ids: list[str] = []
total_chars = 0
for _score, entry_id, display_id, summary in selected:
    line = f"TRW RECALL: [{display_id}] {summary}"
    next_total = total_chars + len(line)
    if next_total > max_chars:
        break
    lines.append(line)
    emitted_ids.append(entry_id)
    total_chars = next_total

if not lines:
    raise SystemExit(0)

injected_file.parent.mkdir(parents=True, exist_ok=True)
with injected_file.open("a", encoding="utf-8") as handle:
    for entry_id in emitted_ids:
        handle.write(f"{entry_id}\n")

sys.stdout.write("\n".join(lines))
PY
) || true

if [ -n "$_recall_output" ]; then
  printf '%s\n' "$_recall_output"
  _phase_status="emitted"
fi

log_hook_execution "UserPromptSubmit" "$_phase" "$_phase_status"
exit 0
