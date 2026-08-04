#!/usr/bin/env bash
# TRW afterMCPExecution hook — observer only; logs best-effort; emits {} on stdout.
# failClosed: false — crash or timeout is safe (observer, not a gate).
#
# Payload parsing is single-path POSIX awk, byte-identical to the extractor in
# trw-before-shell.sh. The old jq-with-`grep -oP`-fallback form logged nothing
# useful on macOS/BSD/busybox, where `grep -P` does not exist.
set -euo pipefail

PAYLOAD="$(cat)"

# Best-effort structured log to .trw/logs/cursor-hooks.jsonl
LOG_DIR="${CURSOR_PROJECT_DIR:-$(pwd)}/.trw/logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
TS="$(date -Iseconds 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)"
EVENT_NAME="afterMCPExecution"

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

# _json_escape: Escape a string for safe embedding as a JSON string value.
# Derived from hooks/lib-trw.sh. The trailing `|| printf ''` keeps a missing
# sed/awk/tr from aborting the hook under `set -euo pipefail`.
_json_escape() {
  printf '%s' "$1" \
    | sed 's/\\/\\\\/g; s/"/\\"/g' \
    | awk 'BEGIN{ORS=""} {gsub(/\t/,"\\t"); if(NR>1)printf "\\n"; printf "%s",$0}' \
    | tr -d '\000-\010\013\014\016-\037\177' \
    || printf ''
}

# "unknown" means the field was absent; "unparsed" means it was there and we
# could not read it. Never conflate the two.
_TOOL_RC=0
TOOL="$(printf '%s' "$PAYLOAD" | _trw_json_scalar tool_name)" || _TOOL_RC=$?
case "$_TOOL_RC" in
    0) ;;
    3) TOOL="unknown" ;;
    *) TOOL="unparsed" ;;
esac
TOOL="$(_json_escape "$TOOL")"

echo "{\"ts\":\"$TS\",\"event\":\"$EVENT_NAME\",\"tool\":\"$TOOL\"}" >> "$LOG_DIR/cursor-hooks.jsonl" 2>/dev/null || true

# Observer: emit empty JSON object (Cursor ignores content for observer hooks)
echo '{}'
