#!/usr/bin/env bash
# TRW afterShellExecution hook — observer only; logs command + duration.
# failClosed: false — crash or timeout is safe (observer, not a gate).
#
# Payload parsing is single-path POSIX awk, byte-identical to the extractor in
# trw-before-shell.sh. The old jq-with-`grep -oP`-fallback form logged an empty
# command on macOS/BSD/busybox (no `grep -P`) and truncated at the first
# JSON-escaped quote elsewhere. No permission is decided here, so the failure was
# lost telemetry rather than a fail-open — but it is the same defect, and the
# gate and the observers must agree on what the command was.
set -euo pipefail

PAYLOAD="$(cat)"

# Best-effort structured log
LOG_DIR="${CURSOR_PROJECT_DIR:-$(pwd)}/.trw/logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
TS="$(date -Iseconds 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)"

# Portable top-level JSON scalar extractor (POSIX awk only — no jq, no grep -P).
# $1 = field name; payload on stdin; decoded value on stdout.
# Exit: 0 = extracted, 2 = present but unparseable, 3 = absent or JSON null.
_trw_json_scalar() {
    awk -v TRW_FIELD="$1" '
      { s = s $0 "\n" }
      END {
        key = "\"" TRW_FIELD "\""
        i = index(s, key)
        if (i == 0) exit 3
        j = i + length(key); n = length(s)
        while (j <= n && substr(s, j, 1) ~ /[ \t\r\n]/) j++
        if (substr(s, j, 1) != ":") exit 2
        j++
        while (j <= n && substr(s, j, 1) ~ /[ \t\r\n]/) j++
        if (j > n) exit 2
        if (substr(s, j, 4) == "null") exit 3
        if (substr(s, j, 1) != "\"") {
          out = ""
          while (j <= n) {
            c = substr(s, j, 1)
            if (c == "," || c == "}" || c == "]") break
            out = out c
            j++
          }
          sub(/[ \t\r\n]+$/, "", out)
          printf "%s", out
          exit 0
        }
        j++
        out = ""
        while (j <= n) {
          c = substr(s, j, 1)
          if (c == "\\") {
            d = substr(s, j + 1, 1)
            if (d == "") exit 2
            if (d == "u") { out = out " "; j += 6; continue }
            if (d == "n" || d == "t" || d == "r" || d == "b" || d == "f") out = out " "
            else out = out d
            j += 2
            continue
          }
          if (c == "\"") { printf "%s", out; exit 0 }
          out = out c
          j++
        }
        exit 2
      }
    '
}

# Observer telemetry: report what we actually know. "unknown" means the field was
# absent; "unparsed" means it was there and we could not read it. Never conflate.
_trw_field() {
    _tf_rc=0
    _tf_val="$(printf '%s' "$PAYLOAD" | _trw_json_scalar "$1")" || _tf_rc=$?
    case "$_tf_rc" in
        0) printf '%s' "$_tf_val" ;;
        3) printf 'unknown' ;;
        *) printf 'unparsed' ;;
    esac
}

# _json_escape: Escape a string for safe embedding as a JSON string value.
# Derived from hooks/lib-trw.sh. Load-bearing now that the extractor no longer
# truncates at an escaped quote — command text reaching the log genuinely
# contains " and \. The trailing `|| printf ''` keeps a missing sed/awk/tr from
# aborting the hook under `set -euo pipefail`.
_json_escape() {
  printf '%s' "$1" \
    | sed 's/\\/\\\\/g; s/"/\\"/g' \
    | awk 'BEGIN{ORS=""} {gsub(/\t/,"\\t"); if(NR>1)printf "\\n"; printf "%s",$0}' \
    | tr -d '\000-\010\013\014\016-\037\177' \
    || printf ''
}

CMD="$(_trw_field command)"
DURATION="$(_trw_field duration_ms)"
EXIT_CODE="$(_trw_field exit_code)"

# Truncate command for log readability, then escape for JSON embedding.
CMD_PREFIX="$(_json_escape "${CMD:0:120}")"
DURATION="$(_json_escape "$DURATION")"
EXIT_CODE="$(_json_escape "$EXIT_CODE")"

echo "{\"ts\":\"$TS\",\"event\":\"afterShellExecution\",\"command_prefix\":\"$CMD_PREFIX\",\"duration_ms\":\"$DURATION\",\"exit_code\":\"$EXIT_CODE\"}" >> "$LOG_DIR/cursor-hooks.jsonl" 2>/dev/null || true

# Observer: emit empty JSON object
echo '{}'
