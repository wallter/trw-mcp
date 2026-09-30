#!/usr/bin/env bash
# TRW beforeShellExecution hook — security-critical gate.
# failClosed: true — a crash or timeout causes the shell command to be denied.
# Checks the command string for common secret-leak patterns and for
# destructive git verbs (reset --hard, clean -f, checkout/restore over
# working-tree files, stash drop/clear, push --force).
# Emits {"permission":"deny","user_message":"..."} if either check matches or
# if the command could not be determined; otherwise emits
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
# Project root: Cursor's dir, else the git top level, else pwd -- never a subdirectory the shell happens to be in
# (HOOK-CWD-STATE-LEAK). `|| true` keeps set -e from failing this fail-closed gate outside a repository.
_trw_root="${CURSOR_PROJECT_DIR:-}"
[ -n "$_trw_root" ] || _trw_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$_trw_root" ] || _trw_root="$(pwd)"
LOG_DIR="$_trw_root/.trw/logs"
# PRD-SEC/RC8: refuse to log through a symlinked .trw, .trw/logs or log file -- a
# crafted checkout must not be able to redirect this append at an arbitrary
# target the user can write. Logging is best-effort (never blocks this
# script's own ALLOW/DENY decision), so the fix is to silently drop the log
# line by redirecting it to /dev/null, not to abort.
if [ -L "${LOG_DIR%/logs}" ] || [ -L "$LOG_DIR" ] || [ -L "$LOG_DIR/cursor-hooks.jsonl" ]; then
    LOG_DIR="/dev/null-trw-logs-disabled"
fi
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
            if (d == "n" || d == "r") out = out "\n"
            else if (d == "t" || d == "b" || d == "f") out = out " "
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

# Destructive-git guard (HB-2: never discard uncommitted work without explicit
# authorization). .cursor/cli.json must allow Shell(git) for everyday git, and
# cursor-agent's Shell(<cmd>) permission token matches the command's FIRST word
# only, so a "Shell(git reset)" deny cannot single out a verb (it would read as
# "deny git"). This hook sees the whole command string, so the verb check lives
# here.
#
# Design: an ALLOW-LIST of argument shapes per risky verb, never a list of bad
# spellings. git accepts abbreviated long options (--har is --hard) and options
# can hide inside other options' values (clean -e -n), so a deny-list of
# spellings is always one spelling short. Instead:
#   reset    only --soft/--mixed/--quiet/--no-refresh/-N/-q and positionals;
#   clean    only -d/-f/-n/-q/-x/-X/--dry-run/--quiet, AND a dry run;
#   restore  only --staged/--source/--quiet (-S/-s/-q), AND --staged;
#   checkout only `-b|-B <new> [<start>]` or `-` (switch branches with git switch);
#   switch   anything but --force/-f/--discard-changes;
#   stash    anything but drop/clear;
#   push     anything but --force/-f/--mirror/+refspec (--force-with-lease is fine).
# Long options match by prefix, as git's own abbreviation does. Every "git"
# token (or a path ending in /git) is scanned to the next shell separator
# (; & | ( ) ` or a newline), so a "git" that is only an argument can add a
# denial, never hide one. Not a shell parser: aliases, eval, variables and
# wrappers that rename git are out of scope.
# Exit: 0 = a risky shape found (phrase on stdout), 1 = none, other = the scan
# could not run (fail closed below).
_trw_destructive_git() {
    awk -v Q="'" '
      function pre(x, name) { sub(/=.*/, "", x); return length(x) >= 3 && index(name, x) == 1 }
      # Short clusters reach verdict() already cut at the first value-taking
      # letter (see the parse loop), so "-sS" arrives as "-s" and "-fcX" as "-fc".
      function short_has(x, ch) { return x ~ /^-[^-]/ && index(x, ch) > 0 }
      function short_only(x, set,   c) {
        if (x !~ /^-[^-]/) return 0
        for (c = 2; c <= length(x); c++) if (index(set, substr(x, c, 1)) == 0) return 0
        return 1
      }
      function verdict(verb,   k, o, ok, dry, staged) {
        if (verb == "reset") {
          for (k = 1; k <= no; k++) { o = opt[k]
            if (!(pre(o, "--soft") || pre(o, "--mixed") || pre(o, "--quiet") || pre(o, "--no-refresh") || pre(o, "--intent-to-add") || short_only(o, "qN")))
              return "git reset " o
          }
        } else if (verb == "clean") {
          dry = 0
          for (k = 1; k <= no; k++) { o = opt[k]
            if (short_only(o, "defnqxX")) { if (index(o, "n")) dry = 1; continue }
            if (pre(o, "--dry-run")) { dry = 1; continue }
            if (!(pre(o, "--quiet") || pre(o, "--exclude"))) return "git clean " o
          }
          if (!dry) return "git clean without --dry-run"
        } else if (verb == "restore") {
          staged = 0
          for (k = 1; k <= no; k++) { o = opt[k]
            if (short_only(o, "Sqs")) { if (index(o, "S")) staged = 1; continue }
            if (pre(o, "--staged")) { staged = 1; continue }
            if (!(pre(o, "--source") || pre(o, "--quiet"))) return "git restore " o
          }
          if (!staged) return "git restore of working-tree files"
        } else if (verb == "checkout") {
          ok = (na == 1 && arg[1] == "-")
          if ((arg[1] == "-b" || arg[1] == "-B") && (na == 2 || na == 3) && arg[2] !~ /^-/ && (na == 2 || arg[3] !~ /^-/)) ok = 1
          if (!ok) return "git checkout (use git switch to change branches)"
        } else if (verb == "switch") {
          for (k = 1; k <= no; k++) { o = opt[k]
            if (short_has(o, "f") || pre(o, "--force") || pre(o, "--discard-changes")) return "git switch " o
          }
        } else if (verb == "stash") {
          for (k = 1; k <= np; k++) if (pos[k] == "drop" || pos[k] == "clear") return "git stash " pos[k]
        } else if (verb == "push") {
          for (k = 1; k <= no; k++) { o = opt[k]; sub(/=.*/, "", o)
            if (o == "--force-with-lease" || o == "--force-if-includes") continue
            if (short_has(o, "f") || pre(o, "--force") || pre(o, "--mirror")) return "git push " o
          }
          for (k = 1; k <= np; k++) if (pos[k] ~ /^\+/) return "git push " pos[k]
        }
        return ""
      }
      { s = s " ; " $0 }
      END {
        gsub(/[;&|()`]/, " ; ", s)
        gsub("[\"" Q "]", "", s)
        n = split(s, t, /[ \t\r\n]+/)
        for (i = 1; i <= n; i++) {
          if (t[i] != "git" && t[i] !~ /\/git$/) continue
          j = i + 1
          while (j <= n && t[j] ~ /^-/ && t[j] != "--") {
            if (t[j] ~ /^(-C|-c|--git-dir|--work-tree|--namespace|--super-prefix|--config-env|--exec-path)$/) j++
            j++
          }
          if (j > n || t[j] == ";") continue
          na = 0; no = 0; np = 0; endopt = 0; verb = t[j]
          # Value-taking options per verb: a short letter ends its cluster (the rest,
          # or the next token, is the value); a long one without "=" takes the next token.
          vs = ""; vl = ""
          if (verb == "restore") { vs = "s"; vl = "--source" }
          else if (verb == "clean") { vs = "e"; vl = "--exclude" }
          else if (verb == "switch") { vs = "cC"; vl = "--create --force-create --orphan" }
          else if (verb == "push") { vs = "o"; vl = "--push-option --repo --receive-pack --exec" }
          else if (verb == "reset") { vl = "--pathspec-from-file" }
          for (k = j + 1; k <= n && t[k] != ";"; k++) {
            arg[++na] = t[k]
            if (!endopt && t[k] == "--") { endopt = 1; continue }
            if (!endopt && t[k] ~ /^-[^-]/) {
              x = t[k]
              for (c = 2; c <= length(x); c++) if (index(vs, substr(x, c, 1))) break
              if (c == length(x) && k < n && t[k + 1] != ";") k++
              opt[++no] = substr(x, 1, c <= length(x) ? c : length(x))
              continue
            }
            if (!endopt && t[k] ~ /^--./ && t[k] !~ /=/) {
              split(vl, L, " "); took = 0
              for (c in L) if (pre(t[k], L[c])) took = 1
              opt[++no] = t[k]
              if (took && k < n && t[k + 1] != ";") k++
              continue
            }
            if (!endopt && t[k] ~ /^-./) opt[++no] = t[k]
            else pos[++np] = t[k]
          }
          hit = verdict(t[j])
          if (hit != "") { printf "%s", hit; exit 0 }
        }
        exit 1
      }
    '
}

GIT_RC=0
GIT_HIT="$(printf '%s' "$CMD" | _trw_destructive_git)" || GIT_RC=$?
if [ "$GIT_RC" = "0" ]; then
    _trw_deny "$GIT_HIT can discard uncommitted work or published history (HB-2), so this hook blocks it. Ask the user; if they want it, they can run it in their own terminal."
elif [ "$GIT_RC" != "1" ]; then
    _trw_deny "destructive-git scan could not run (failing closed)"
fi

echo '{"permission":"allow"}'
