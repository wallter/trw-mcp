#!/bin/sh
# PRD-INFRA-024-FR02 + PRD-CORE-095 + PRD-FIX-124 + PRD-CORE-247: UserPromptSubmit hook.
# Three independent limbs, deliberately NOT coupled:
#
#   1. Phase guidance (PRD-CORE-095 FR01-FR06) — one calibrated line per phase
#      TRANSITION. Suppressed when the phase is unchanged, and silent at "done".
#   2. Auto-recall (PRD-CORE-095 FR07-FR14, repaired by PRD-FIX-124) — surfaces
#      a stored learning matching the prompt. Runs on EVERY prompt: it takes no
#      input from infer_phase, because a delivered run does not mean the next
#      prompt needs no context (PRD-FIX-124 FR03/FR04).
#   3. Absent-MCP-surface detection (PRD-CORE-247 FR01/FR02) — when no trw_ tool
#      invocation has been observed since the SessionStart epoch marker, past a
#      grace window and a prompt threshold, prints the offline protocol ONCE per
#      session. Fail-open toward "the surface is present": every unreadable input
#      yields silence.
#
# Fail-open: never blocks prompts. Output target: <150 tokens per phase line
# plus at most auto_recall_max_tokens of recall.
#
# Performance: ~71ms baseline (infer_phase); a full 6.4k-entry scan + IDF scan
# measures ~190ms warm against the 500ms deadline (PRD-FIX-124 NFR01).
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

# PRD-FIX-124 FR11: hand infer_phase THIS session's identity so it resolves the
# run we own instead of whichever run sorted newest project-wide. Same grep-based
# extraction phase-cycle-stop.sh uses; empty is the honest "identity unknown"
# state, which infer_phase degrades to its recency fallback.
_stdin_session_id=$(printf '%s' "$_payload" \
  | grep -o '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' \
  | head -1 \
  | sed 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/') || _stdin_session_id=""

_phase=$(infer_phase "$_stdin_session_id")
_project_root="$(get_repo_root)" || exit 0
_context_dir="$_project_root/.trw/context"
[ -d "$_context_dir" ] || mkdir -p "$_context_dir" 2>/dev/null || true

# FR07: malformed or missing prompt input must stay fully silent.
if [ -z "$_prompt" ]; then
  log_hook_execution "UserPromptSubmit" "$_phase" "skipped"
  exit 0
fi

# --- PRD-CORE-247-FR01/FR02: absent-MCP-surface detection (third limb) ---
#
# A THIRD limb, decoupled from the other two exactly as they are from each other.
# It runs before the phase-guidance limb because when it fires, its block is the
# only guidance worth reading: everything below names trw_ tools this session
# does not have.
#
# The detector, the tunables, and the emitted text all live in lib-trw.sh. The
# `command -v` guard is not defensive clutter — a project whose .claude/hooks/
# copy of lib-trw.sh predates PRD-CORE-247 must keep working, and the fail-open
# direction for this whole subsystem is "the surface is present" (silence).
#
# PRD-FIX-128-FR06: every one of the four calls takes THIS session's identity.
# `_stdin_session_id` is the same value the other two limbs already use, and
# `trw_pin_key` prefers the exported session variable over it, so precedence is
# unchanged. Threading it is what makes the keyed epoch, the keyed latch, and
# the owned-run scan reachable on a client whose profile publishes no session
# variable -- which is every profile except claude-code, and therefore every
# tree whose generated hook-env.sh was written by one of them. An older library
# that predates FR128 ignores the extra argument, so the guard above still holds.
_degraded_emitted=0
if command -v trw_bump_session_prompt_index >/dev/null 2>&1; then
  _prompt_index=$(trw_bump_session_prompt_index "$_stdin_session_id")
  if ! trw_degraded_latched "$_stdin_session_id" \
    && trw_degraded_mode_detected "$_prompt_index" "$_stdin_session_id"; then
    trw_emit_offline_protocol_block \
      "$(trw_session_epoch_ts "$_stdin_session_id" 2>/dev/null || printf '')" "$_stdin_session_id"
    echo ""
    _degraded_emitted=1
  fi
fi

# --- PRD-CORE-095 FR01-FR06: Phase-change suppression (phase-guidance limb) ---
_phase_cache="$_context_dir/last_ups_phase"
_injected_file="$_context_dir/injected_learning_ids.txt"

# FR04: "none" phase always emits (agent needs session_start reminder)
if [ "$_phase" != "none" ]; then
  _cached_phase=""
  if [ -f "$_phase_cache" ]; then
    _cached_phase=$(cat "$_phase_cache" 2>/dev/null) || true
  fi
  # FR02: Same-phase suppression — skip PHASE OUTPUT if unchanged. PRD-FIX-124
  # FR04 narrows this to the guidance limb; auto-recall below never reads it.
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
_emitted_any=$_degraded_emitted
if [ "$_phase_suppressed" = "0" ]; then
  case "$_phase" in
    none)
      echo "TRW: Call trw_session_start(query='your task domain') to load context, then read .trw/frameworks/FRAMEWORK-CORE.md — it defines the methodology your tools implement."
      _emitted_any=1
      ;;
    early)
      echo "TRW [RESEARCH/PLAN]: PRD validation gates implementation — trw_prd_validate catches ambiguity before it becomes rework."
      _emitted_any=1
      ;;
    plan)
      echo "TRW [PLAN]: Run trw_prd_validate before implementing — a spec gap found now is an edit; found during implementation it is a rewrite."
      _emitted_any=1
      ;;
    implement)
      echo "TRW [IMPLEMENT]: Before completing, re-read FRs for coverage gaps. Call trw_checkpoint after milestones — uncheckpointed work is lost on compaction."
      _emitted_any=1
      ;;
    validate)
      echo "TRW [VALIDATE]: Run applicable project-native checks, then record observed counts/status with trw_build_check(tests_passed, test_count, failure_count, static_checks_clean, scope). The reporter does not run checks."
      _emitted_any=1
      ;;
    deliver)
      echo "TRW [DELIVER]: trw_deliver() persists learnings, syncs the client instruction file, and closes the run — without it, your session's work is invisible to future agents."
      _emitted_any=1
      ;;
    done)
      # Silent — the RUN is complete, so there is no next phase to guide toward
      # (PRD-CORE-095 FR06). This silence is scoped to the guidance limb only;
      # PRD-FIX-124 FR03 keeps auto-recall running below.
      ;;
  esac
fi

# --- PRD-CORE-095 FR07-FR14 / PRD-FIX-124: Contextual learning auto-injection ---

# FR14: Config gate — check auto_recall_enabled. Every value below mirrors a
# typed TRWConfig field; the inline literals are the no-config-file fallbacks
# and are asserted equal to the Pydantic defaults by
# tests/test_auto_recall_scoring.py::test_default_threshold_is_recalibrated.
_auto_recall_enabled="true"
_auto_recall_max_results=3
_auto_recall_max_tokens=100
_auto_recall_min_score="0.35"
_auto_recall_scan_cap=10000
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
  _val=$(grep -m1 'auto_recall_scan_cap:' "$_config_file" 2>/dev/null | sed 's/.*: *//' | tr -d "\"'" 2>/dev/null) || true
  [ -n "$_val" ] && _auto_recall_scan_cap="$_val"
fi
# Env var overrides
[ -n "$TRW_AUTO_RECALL_ENABLED" ] && _auto_recall_enabled="$TRW_AUTO_RECALL_ENABLED"
[ -n "$TRW_AUTO_RECALL_MAX_RESULTS" ] && _auto_recall_max_results="$TRW_AUTO_RECALL_MAX_RESULTS"
[ -n "$TRW_AUTO_RECALL_MAX_TOKENS" ] && _auto_recall_max_tokens="$TRW_AUTO_RECALL_MAX_TOKENS"
[ -n "$TRW_AUTO_RECALL_MIN_SCORE" ] && _auto_recall_min_score="$TRW_AUTO_RECALL_MIN_SCORE"
[ -n "$TRW_AUTO_RECALL_SCAN_CAP" ] && _auto_recall_scan_cap="$TRW_AUTO_RECALL_SCAN_CAP"

# Early exit if disabled
if [ "$_auto_recall_enabled" = "false" ]; then
  log_hook_execution "UserPromptSubmit" "$_phase" "skipped"
  exit 0
fi

# PRD-FIX-124 FR03: the auto-recall limb runs on exactly three conditions —
# enabled, a non-empty prompt, and an entries directory. Phase is not consulted.
_entries_dir="$_project_root/.trw/learnings/entries"
if [ ! -d "$_entries_dir" ]; then
  log_hook_execution "UserPromptSubmit" "$_phase" "skipped"
  exit 0
fi

# FR05: the scorer's diagnostic goes to stderr (stdout is injected into the
# model's context and must carry recall text only). Capture it so it can also be
# forwarded to the durable hook log, then replay it for interactive debugging.
_diag_file="$_context_dir/.auto_recall_diag.$$"
_recall_output=$(
  python3 - "$_entries_dir" "$_prompt" "$_injected_file" "$_auto_recall_max_results" \
    "$_auto_recall_max_tokens" "$_auto_recall_min_score" "$_auto_recall_scan_cap" \
    2>"$_diag_file" <<'PY'
from __future__ import annotations

import math
import re
import sys
import time
from pathlib import Path

START_NS = time.monotonic_ns()
TIMEOUT_NS = 500_000_000
MAX_KEYWORDS = 16
MIN_TOKEN_LEN = 4
# Inline mirrors of the typed TRWConfig defaults. A POSIX hook cannot import
# Pydantic, so these are a third declaration site alongside
# models/config/_fields_build.py and models/config/_sub_models.py; the three are
# asserted equal by tests/test_auto_recall_scoring.py.
DEFAULT_MAX_RESULTS = 3
DEFAULT_MAX_TOKENS = 100
DEFAULT_MIN_SCORE = 0.35
DEFAULT_SCAN_CAP = 10000
TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
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


def _unquote(value: str) -> str:
    """Strip a matching pair of YAML quotes and undo that style's escape.

    Applied to a scalar ONCE, after any folded continuation lines have been
    joined: a folded quoted summary opens its quote on the first line and closes
    it several lines later, so stripping per line would never match.
    """
    if len(value) >= 2 and value[0] == value[-1]:
        if value[0] == "'":
            return value[1:-1].replace("''", "'")
        if value[0] == '"':
            return value[1:-1].replace('\\"', '"')
    return value


def _parse_entry(path: Path) -> tuple[str, str, str, list[str]]:
    """Read one learning entry ONCE and return (status, id, summary, tags).

    Entries are flat YAML: the four fields this hook depends on are top-level
    keys at column 0 (see docs/documentation/operational-knowledge/
    auto-recall-calibration.md for the read-model contract). ``summary`` may be
    a folded scalar whose continuation lines are indented; ``tags`` is a block
    sequence of ``- item`` lines. Any parse failure yields empty values so the
    entry simply scores zero (NFR02).
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", "", "", []
    status = ""
    entry_id = ""
    summary_parts: list[str] = []
    tags: list[str] = []
    mode = ""
    for line in text.splitlines():
        if mode == "summary":
            if line[:1] in {" ", "\t"}:
                summary_parts.append(line.strip())
                continue
            mode = ""
        elif mode == "tags":
            stripped = line.lstrip()
            if stripped[:1] == "-":
                item = _unquote(stripped[1:].strip())
                if item:
                    tags.append(item)
                continue
            mode = ""
        first = line[:1]
        if not first or first in {" ", "\t", "-", "#"}:
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        if key == "status":
            status = _unquote(value.strip())
        elif key == "id":
            entry_id = _unquote(value.strip())
        elif key == "summary":
            scalar = value.strip()
            summary_parts = [scalar] if scalar else []
            mode = "summary"
        elif key == "tags":
            inline = value.strip()
            tags = []
            if not inline:
                mode = "tags"
    summary = _unquote(" ".join(p for p in summary_parts if p).strip())
    return status, entry_id, summary, tags


def _tokenize(text: str) -> set[str]:
    return {
        word
        for word in TOKEN_RE.findall(text.lower())
        if len(word) >= MIN_TOKEN_LEN and word not in STOP_WORDS
    }


def _as_int(raw: str, fallback: int, minimum: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return value if value >= minimum else fallback


def _as_float(raw: str, fallback: float, low: float, high: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback
    return value if low <= value <= high else fallback


entries_dir = Path(sys.argv[1])
prompt = sys.argv[2]
injected_file = Path(sys.argv[3])
max_results = _as_int(sys.argv[4], DEFAULT_MAX_RESULTS, 0)
max_tokens = _as_int(sys.argv[5], DEFAULT_MAX_TOKENS, 0)
min_score = _as_float(sys.argv[6], DEFAULT_MIN_SCORE, 0.0, 1.0)
scan_cap = _as_int(sys.argv[7], DEFAULT_SCAN_CAP, 1)
max_chars = max_tokens * 4
deadline_ns = START_NS + TIMEOUT_NS

keywords: list[str] = []
for word in TOKEN_RE.findall(prompt.lower()):
    if len(word) < MIN_TOKEN_LEN or word in STOP_WORDS or word in keywords:
        continue
    if len(keywords) >= MAX_KEYWORDS:
        break
    keywords.append(word)


def _diagnostic(decision: str, scanned: int, top_score: float, top_id: str, injected: int) -> None:
    """FR05: one machine-readable record per scorer run, on stderr only.

    NFR03: counts, scores and learning IDs only — never prompt or detail text.
    """
    elapsed_ms = (time.monotonic_ns() - START_NS) // 1_000_000
    sys.stderr.write(
        f"event=AutoRecall keywords={len(keywords)} scanned={scanned}"
        f" top_score={top_score:.3f} top_id={top_id or 'none'}"
        f" threshold={min_score:.3f} injected={injected}"
        f" decision={decision} elapsed_ms={elapsed_ms}\n"
    )


if not keywords or not entries_dir.is_dir():
    _diagnostic("no_keywords", 0, 0.0, "", 0)
    raise SystemExit(0)

injected_ids: set[str] = set()
if injected_file.is_file():
    injected_ids = {
        line.strip()
        for line in injected_file.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    }

# FR07: the cap is a typed tunable, not an age filter. At its default it covers
# a whole store this size; the most-recently-modified ordering survives only as
# the tie-break for a store that still exceeds the raised cap.
all_entries = list(entries_dir.glob("*.yaml"))
if len(all_entries) > scan_cap:

    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    all_entries.sort(key=_mtime, reverse=True)
    all_entries = all_entries[:scan_cap]

# Pass 1: read each entry once, keep only the prompt keywords it contains, and
# accumulate document frequencies over the population actually tokenized. FR08:
# a deadline stops the scan here, and everything already collected still scores.
candidates: list[tuple[str, str, str, frozenset[str]]] = []
doc_freq: dict[str, int] = dict.fromkeys(keywords, 0)
doc_count = 0
scanned = 0
deadline_hit = False
for entry in sorted(all_entries):
    if time.monotonic_ns() >= deadline_ns:
        deadline_hit = True
        break
    scanned += 1

    status, entry_id, summary, tags = _parse_entry(entry)
    # FR12: the mirrored status is the candidate gate — a learning retired in
    # SQLite is retired here as soon as the mirror records the transition.
    if status.lower() != "active":
        continue
    entry_id = entry_id or entry.stem
    if entry_id in injected_ids:
        continue
    if not summary:
        continue

    doc_count += 1
    # FR01/FR02: the learning's own token set is its summary PLUS its tags,
    # which carry its most topical, least diluted vocabulary.
    tokens = _tokenize(summary + " " + " ".join(tags))
    matched = frozenset(keyword for keyword in keywords if keyword in tokens)
    if not matched:
        continue
    for keyword in matched:
        doc_freq[keyword] += 1
    display_id = entry_id if entry_id.startswith("L-") else f"L-{entry_id}"
    candidates.append((entry_id, display_id, summary, matched))

# Pass 2: FR01 — score is the IDF-weighted fraction of the PROMPT's keyword mass
# found in the learning's token set, so it no longer decays as the prompt gets
# longer and more specific. Bounded [0.0, 1.0]; exactly 1.0 when every keyword
# is present.
#
# The weight is SMOOTHED — log((N + 1) / (df + 1)) + 1, the standard smooth-idf
# form. The unsmoothed variant sends a term that occurs in every scored document
# to weight zero, which is defensible at scale and catastrophic at small N: on a
# new project's 1-entry store a PERFECT match scores 0.000, because every one of
# its terms is in "every" document. The +1 floor keeps rare terms weighted more
# than common ones while making the small-N case degrade to plain coverage
# rather than to silence.
idf = {
    keyword: math.log((doc_count + 1) / (doc_freq[keyword] + 1)) + 1.0 for keyword in keywords
}
total_idf = sum(idf.values())


def _score(matched: frozenset[str]) -> float:
    if total_idf <= 0.0:
        return len(matched) / len(keywords)
    return sum(idf[keyword] for keyword in matched) / total_idf


scored = [(_score(matched), entry_id, display_id, summary) for entry_id, display_id, summary, matched in candidates]
top_score = 0.0
top_id = ""
for score, entry_id, _display_id, _summary in scored:
    if score > top_score:
        top_score = score
        top_id = entry_id

results = [item for item in scored if item[0] >= min_score]
results.sort(key=lambda item: (-item[0], item[1]))
selected = results[:max_results]

lines: list[str] = []
emitted_ids: list[str] = []
total_chars = 0
for _score_value, entry_id, display_id, summary in selected:
    line = f"TRW RECALL: [{display_id}] {summary}"
    # NFR06: budget the newline that follows each line too, so the block the
    # hook prints — separators and trailing newline included — stays inside
    # auto_recall_max_tokens * 4 characters rather than one byte over per line.
    next_total = total_chars + len(line) + 1
    if next_total > max_chars:
        break
    lines.append(line)
    emitted_ids.append(entry_id)
    total_chars = next_total

if deadline_hit:
    decision = "deadline"
elif lines:
    decision = "fired"
elif top_score <= 0.0:
    decision = "no_match"
else:
    decision = "below_threshold"
_diagnostic(decision, scanned, top_score, top_id, len(lines))

if not lines:
    raise SystemExit(0)

injected_file.parent.mkdir(parents=True, exist_ok=True)
with injected_file.open("a", encoding="utf-8") as handle:
    for entry_id in emitted_ids:
        handle.write(f"{entry_id}\n")

sys.stdout.write("\n".join(lines))
PY
) || true

_diag=""
if [ -f "$_diag_file" ]; then
  _diag=$(grep -m1 '^event=AutoRecall' "$_diag_file" 2>/dev/null) || _diag=""
  cat "$_diag_file" >&2 2>/dev/null || true
  rm -f "$_diag_file" 2>/dev/null || true
fi

if [ -n "$_recall_output" ]; then
  printf '%s\n' "$_recall_output"
  _emitted_any=1
fi

if [ "$_emitted_any" = "1" ]; then
  _phase_status="emitted"
elif [ "$_phase_suppressed" = "1" ]; then
  _phase_status="cached"
else
  _phase_status="silent"
fi

log_hook_execution "UserPromptSubmit" "$_phase" "$_phase_status" "$_diag"
exit 0
