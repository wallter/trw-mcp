#!/usr/bin/env bash
# TRW beforeShellExecution hook — security-critical gate.
# failClosed: true — a crash or timeout causes the shell command to be denied.
# Checks for common secret-leak patterns in the command string.
# Emits {"permission":"deny","user_message":"..."} if a secret pattern is
# detected or if the command could not be determined; otherwise emits
# {"permission":"allow"}.
#
# Payload parsing is deliberately single-path POSIX awk — NOT jq-with-a-fallback.
# The previous two-path form (jq, else `grep -oP`) had two fail-open bypasses:
#   1. `grep -P` is GNU-only. On macOS/BSD/busybox the fallback errored, the
#      command extracted as EMPTY, the secret pattern matched nothing, and the
#      gate emitted "allow" while advertising failClosed: true.
#   2. `[^"]+` stopped at the first JSON-escaped quote, so a command containing
#      \" hid everything after it from the scan — on GNU Linux too.
# Only the jq path was ever exercised by CI, so neither was visible. One path
# that runs everywhere is what keeps the tested behavior and the shipped
# behavior the same.
set -euo pipefail

PAYLOAD="$(cat)"

# Best-effort structured log
LOG_DIR="${CURSOR_PROJECT_DIR:-$(pwd)}/.trw/logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
TS="$(date -Iseconds 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)"

# Portable top-level JSON scalar extractor (POSIX awk only — no jq, no grep -P).
# $1 = field name; payload on stdin; decoded value on stdout.
# Exit: 0 = extracted (value may legitimately be empty)
#       2 = field present but unparseable  -> UNDETERMINED, never "nothing to check"
#       3 = field absent or JSON null      -> genuinely nothing to check
# trw:intentional exit 2 and exit 3 are distinct because the caller must be able
# to tell "there is no command" from "we could not read the command". Collapsing
# them into one empty-string result is the original defect.
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
# Derived from hooks/lib-trw.sh (the cursor hooks do not source it — a security
# gate must not pull code into its deciding shell). Now that the extractor no
# longer truncates at an escaped quote, command text reaching the log genuinely
# contains " and \, so this is load-bearing for valid JSONL.
# The trailing `|| printf ''` matters under `set -euo pipefail`: logging is
# best-effort, and a missing sed/awk/tr must degrade the LOG, never abort the
# gate before it has emitted a decision.
_json_escape() {
  printf '%s' "$1" \
    | sed 's/\\/\\\\/g; s/"/\\"/g' \
    | awk 'BEGIN{ORS=""} {gsub(/\t/,"\\t"); if(NR>1)printf "\\n"; printf "%s",$0}' \
    | tr -d '\000-\010\013\014\016-\037\177' \
    || printf ''
}

# Extract the command. A missing awk (rc 127) lands in the undetermined branch
# below, which denies — the gate never degrades to permissive on tooling gaps.
EXTRACT_RC=0
CMD="$(printf '%s' "$PAYLOAD" | _trw_json_scalar command)" || EXTRACT_RC=$?

CMD_PREFIX_ESC="$(_json_escape "${CMD:0:80}")"
echo "{\"ts\":\"$TS\",\"event\":\"beforeShellExecution\",\"command_prefix\":\"$CMD_PREFIX_ESC\",\"extract_rc\":\"$EXTRACT_RC\"}" >> "$LOG_DIR/cursor-hooks.jsonl" 2>/dev/null || true

_trw_deny() {
    echo "{\"ts\":\"$TS\",\"event\":\"beforeShellExecution\",\"action\":\"deny\",\"reason\":\"$1\"}" >> "$LOG_DIR/cursor-hooks.jsonl" 2>/dev/null || true
    printf '{"permission":"deny","user_message":"TRW: %s"}\n' "$1"
    exit 0
}

# Undetermined extraction must fail CLOSED, matching the failClosed: true header.
# An empty CMD here is not evidence that there is nothing to check — it is the
# absence of an answer, and the absence of an answer is not an allow.
if [ "$EXTRACT_RC" != "0" ] && [ "$EXTRACT_RC" != "3" ]; then
    _trw_deny "could not parse the shell command from the hook payload (failing closed)"
fi

# EXTRACT_RC 3 means the payload carries no command field (or an explicit null):
# genuinely nothing to inspect, so this is a real allow, not a fallback allow.
if [ "$EXTRACT_RC" = "3" ]; then
    echo '{"permission":"allow"}'
    exit 0
fi

# Secret-leak pattern detection — matches assignment-style secrets in command strings
# Patterns: password=VALUE, API_KEY=VALUE, secret=VALUE, token=VALUE
# A "value" must be at least one non-whitespace character.
# -E (POSIX ERE) is portable; -P (PCRE) would not be.
#
# grep exits 0 on match, 1 on a clean scan, and 2 (or 127 when absent) when the
# scan could not run. Only 1 is evidence the command is clean — testing this with
# a bare `if ... grep -q`, as this hook used to, folds "grep is missing" into
# "no secret found" and hands back the same fail-open this file just fixed.
# A here-string, not a pipe, so pipefail cannot report a SIGPIPE'd writer instead
# of grep's own verdict.
SCAN_RC=0
grep -qiE '(password|api_key|secret|token)=[^[:space:]]+' <<<"$CMD" || SCAN_RC=$?
if [ "$SCAN_RC" = "0" ]; then
    _trw_deny "possible secret leak detected in shell command"
elif [ "$SCAN_RC" != "1" ]; then
    _trw_deny "secret-leak scan could not run (failing closed)"
fi

echo '{"permission":"allow"}'
