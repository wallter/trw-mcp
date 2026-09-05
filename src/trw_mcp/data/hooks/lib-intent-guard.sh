#!/bin/sh
# Shared intent-contract control-point routines — PRD-CORE-250-FR05.
#
# Sourced by pre-tool-intent-guard.sh (PreToolUse) and post-tool-intent-check.sh
# (PostToolUse). Those two files were 254 and 255 lines sharing 232 identical
# ones, with four function bodies duplicated verbatim; a signal-trap or timeout
# fix applied to one and missed on the other was a live divergence rather than a
# style complaint. Everything identical lives here now, and each hook keeps only
# its event-specific vocabulary — the six `_trw_*` variables read below.
#
# LIBRARY DISCIPLINE. These are the three rules the one previous "shared hook
# library" in this directory broke, which is why PRD-CORE-250-FR04 deleted it
# rather than repairing it — it declared bash and `set -euo pipefail` at top
# level while every bundled hook is invoked as `sh <path>`, so sourcing it would
# have aborted its own callers:
#   * POSIX sh. No `[[ ]]`, no `local`, no arrays — every caller runs under
#     `sh "$CLAUDE_PROJECT_DIR/.claude/hooks/<name>"`.
#   * No shell options at top level. Sourcing must not impose `errexit` or
#     `nounset` on the caller.
#   * No `exit` outside a function. Sourcing must not be able to end the caller.
#
# TRUST BOUNDARY, stated plainly rather than implied. This file IS sourced into
# the shell that decides, so it is inside that shell's trusted computing base:
# an attacker who can write it can write the decision, exactly as an attacker who
# can write the hook can. That is a different position from `lib-trw.sh`, which
# is shared with ten unrelated hooks and is therefore sourced ONLY in a detached
# subshell (see _trw_telemetry, and review round 3 finding F1, 2026-07-25). Two
# consequences follow and both are implemented:
#   * this file is listed in HOOK_SUPPORT_FILES
#     (security/intent_contract/_control_plane.py), so it is covered by
#     `expected_hook_digest` — a `chmod 000` or a content edit makes enrollment
#     `stale`, which the Python entry point fails closed on (probe finding N6);
#   * each hook guards its `.` of this file and, when the source fails, decides
#     for itself rather than falling through. See the degraded block there.

# _trw_recognize_fs: walk up from $1 looking for the enrollment marker or the
# durable enrollment-evidence file. Builtins only — no fork, no git — so a hook
# can call it before installing its traps, which is what closes the F-C window
# (a SIGTERM in the preamble used to exit 0 from an enrolled, violating project).
#
# `$PWD` and `$CLAUDE_PROJECT_DIR` are both walked because the git-derived root
# is NOT trustworthy for this question: a stub whose `--show-toplevel` prints an
# empty directory pointed both `[ -f ]` tests at the wrong tree and disarmed the
# hook (finding F-G). Adding candidate roots can only ever turn recognition ON,
# so a lying git can no longer turn it off.
# The directory the marker was found in is remembered in _trw_enrolled_root and
# handed to the Python control point as TRW_PROJECT_ROOT (unless the caller set
# it): the Python side resolves its root from that variable or the cwd, and a
# hook fired from a subdirectory (a sub-agent that `cd`-ed into a package) used
# to read the marker relative to that subdirectory, find it "missing but
# tracked", and block every write as `stale` (observed 2026-09-04, three
# agents in one session).
_trw_enrolled_root=""
_trw_recognize_fs() {
  _trw_scan="$1"
  while [ -n "$_trw_scan" ] && [ "$_trw_scan" != "/" ]; do
    if [ -f "$_trw_scan/$_trw_marker" ] || [ -f "$_trw_scan/$_trw_evidence" ]; then
      _trw_enrolled_root="$_trw_scan"
      return 0
    fi
    case "$_trw_scan" in
      */*) _trw_scan="${_trw_scan%/*}" ;;
      *) return 1 ;;
    esac
  done
  return 1
}

# Record a decision. The CODE is assigned before the flag that publishes it: with
# the two lines the other way round, a signal arriving between them found
# `_trw_exit_decided=1` next to a stale `_trw_decided_code=0` and converted a
# block into an allow (finding F-C, latent — 0 hits in 150 timed runs, one line
# to remove).
_trw_decide() {
  _trw_decided_code="$1"
  _trw_exit_decided=1
}

# A catchable signal is an undecided exit too, and the EXIT trap alone does not
# reliably cover it: a signal-killed shell reports 128+signum, which is not 2 and
# therefore does not block.
_trw_on_signal() {
  if [ "$_trw_exit_decided" = "1" ]; then
    exit "$_trw_decided_code"
  fi
  _trw_decide 2
  if [ "$_trw_enrolled" = "1" ]; then
    printf 'BLOCKED (intent-contract): %s was terminated by a signal before it could decide.\n' \
      "$_trw_subject" >&2 || true
    exit 2
  fi
  _trw_decide 0
  exit 0
}

# --- the shared lib never runs where it can delay or change a decision -------
# `. lib-trw.sh` in the DECIDING shell handed a tampered lib the hook's own
# control flow. Making the trap fail closed only covered libs that ABORT; a lib
# that parses fine and merely assigns `_trw_enrolled=0`, pre-sets
# `_trw_exit_decided=1`, or defines `timeout() { return 0; }` — one appended line
# each — silently produced exit 0 from an enrolled control point (finding F1,
# 2026-07-25). Running it in a subshell fixed WHAT it could write and left WHEN
# it could return: both remaining call sites were synchronous and unbounded, so
# `trap "" TERM HUP INT QUIT; sleep 120` in the lib stopped the hook reaching any
# exit at all (finding F-A).
#
# So lib-trw.sh is reduced to what it is actually for here — best-effort
# telemetry — and that runs DETACHED. A backgrounded subshell cannot write this
# shell's variables, cannot define functions here, its `exit` ends itself, and it
# cannot hold the decision open for even one scheduler tick. Its three standard
# descriptors are redirected so it can never hold the client's pipe open either.
# `init_hook_timer` is inlined as `date +%s` by the caller so the duration stays
# real.
#
# The probe that used to read HOOKS_ENABLED out of the lib is GONE rather than
# bounded, because it never had a live output: HOOKS_ENABLED was consulted only
# when `_trw_enrolled=0`, and an unenrolled project exits 0 a few lines further
# down regardless. Its failure branch — block when the lib cannot be sourced —
# was redundant too: `expected_hook_digest` covers lib-trw.sh, so ANY tamper
# (content or mode) makes enrollment `stale`, which the python entry point fails
# closed on. The hook no longer depends on that lib to decide anything.
_trw_telemetry() {
  (
    _hook_start_epoch="$_trw_started"
    . "$_hook_dir/lib-trw.sh" >/dev/null 2>&1 || exit 0
    log_hook_execution "$1" "$2" "$3"
  ) >/dev/null 2>&1 </dev/null &
}

# --- git legs of enrollment recognition --------------------------------------
# The marker's absence alone is NOT proof of "never enrolled": a plain `rm` in
# the working tree would otherwise disarm every control point. Two durable second
# signals; the first (the enrollment-evidence file, written at first enrollment
# and never rewritten) is the `[ -f ]` walk above, which answers with no git at
# all — which is what makes a shadowed `git` stub in a writable PATH directory
# useless as a disarm (probe finding N10). The second is git history, anchored to
# HEAD rather than the index, because the index is writable by the same attacker
# (`git rm -f <marker>` clears file and index entry in one uncommitted command;
# probe finding N3). The index is still consulted as a strictly-additional
# signal, so a staged-but-uncommitted marker also counts.
#
# When BOTH are absent and git cannot answer, this project is treated as never
# enrolled and the hook stays inert. That REVERSES the earlier ruling that any
# unanswerable git means "stay armed", whose premise — that `.git` present plus a
# failing `rev-parse` is pathological — was simply wrong: a `.git` FILE pointing
# at a pruned worktree or deinit'd submodule, a partial clone, a syntax error in
# the user's global ~/.gitconfig, and dubious-ownership under a Docker/CI bind
# mount or a sudo-created clone all produce it with nobody attacking anything.
# These hooks are registered unconditionally on every installed project, so that
# ruling blocked every Write/Edit for users who never opted in. What it was
# defending is preserved by the filesystem signal.
#
# RESIDUAL: deleting the evidence file AND the marker AND keeping git from
# answering disarms this. That is three acts rather than two, the evidence file
# is git-tracked (a committed removal is a C9 finding) and it lives outside
# .trw/contracts, so `rm -rf .trw/contracts` does not take it. The risk in the
# other direction — blocking every write for every never-enrolled user with a
# typo in their gitconfig — is both larger and certain.
_trw_recognize_slow_legs() {
  if [ "$_trw_enrolled" = "0" ]; then
    # `$PWD` is required to name the real cwd at shell startup and both /bin/sh
    # and dash recompute a stale inherited value — but `pwd` is the definition
    # rather than a cached copy of it, and re-walking from it costs one fork in a
    # project we have not recognized yet. It runs HERE, after the traps, because
    # a fork before them would re-open the window finding F-C closed.
    if _trw_recognize_fs "$(pwd)"; then
      _trw_enrolled=1
    fi
  fi

  if [ "$_trw_enrolled" = "0" ]; then
    _trw_root="${CLAUDE_PROJECT_DIR:-}"
    if [ -z "$_trw_root" ]; then
      _trw_root="$(git rev-parse --show-toplevel 2>/dev/null)" || _trw_root=""
    fi
    [ -n "$_trw_root" ] || _trw_root="$PWD"
    if [ -e "$_trw_root/.git" ] && git -C "$_trw_root" rev-parse --git-dir >/dev/null 2>&1; then
      if git -C "$_trw_root" cat-file -e "HEAD:$_trw_marker" 2>/dev/null; then
        _trw_enrolled=1
        _trw_enrolled_root="$_trw_root"
      elif git -C "$_trw_root" ls-files --error-unmatch "$_trw_marker" >/dev/null 2>&1; then
        _trw_enrolled=1
        _trw_enrolled_root="$_trw_root"
      fi
    fi
  fi
}

# --- PRD-CORE-254: the glob-sidecar fast path --------------------------------
# Both hooks used to spawn a fresh interpreter on EVERY Write/Edit/MultiEdit --
# 0.49s pre-write plus 0.50s post-edit, measured 2026-09-03 -- just to learn that
# the edited path is anchored by no claim. On a loaded box that spawn crosses the
# 1s budget and `timeout` returns 124, which the fail-closed mapping below turns
# into exit 2: the write is blocked by CONTENTION rather than by policy.
#
# The anchor set only changes when an operator edits the contract, so enrollment
# pre-renders it into `.trw/contracts/enrollment.globs` (see _sidecar.py) and this
# code answers the common case with builtins plus one `jq`.
#
# ONE-DIRECTIONAL by construction, which is the whole safety argument: the fast
# path may only ever turn a would-run-Python into `exit 0`, and only on a proven
# no-match. It cannot block, cannot decide a matching path, and cannot widen the
# claim set. Anything it cannot cheaply prove -- no jq, an unusable sidecar, a
# path shaped like it might be an alias, a pattern hit -- falls through to
# _trw_resolve_and_run_python exactly as before, which costs latency and never
# correctness.
_trw_globs=".trw/contracts/enrollment.globs"
_trw_nl='
'

# _trw_fast_target: set _trw_fp_rel to the repo-relative path this payload edits,
# or return non-zero for "defer to Python".
#
# EXTRACTION. `jq` is the only reader, and it is a real JSON parser rather than a
# grep/sed guess: this decision has security consequences that
# instructions-loaded.sh's jq-absent grep fallback for its own file_path does
# not, and a two-path parser whose second path CI never exercises is where the
# `.cursor` hook's fail-open lived (2026-07-29). No jq, no fast path.
#
# The jq program is also the validator, so the shell never reasons about a value
# a parser has not already vouched for: MultiEdit/NotebookEdit carry several
# paths rather than one scalar and are dropped outright; the result must be a
# STRING, printable ASCII with no SPACE, and free of `"` and `\` -- a backslash
# is an escape to POSIX `case` and a literal to Python's fnmatch, which is
# precisely the kind of divergence a shell fast path must refuse rather than
# resolve, and a space is excluded for the same reason the `..` shapes are: the
# fast path decides only algebraically simple paths and defers the rest, at the
# cost of one Python spawn on a file nobody names that way.
#
# The whole-payload byte scan the PRD sketched is deliberately applied to the
# extracted VALUE instead. An Edit payload carries `old_string`/`new_string` --
# the file's own source text -- so a printable-ASCII-and-no-backslash gate over
# the raw payload rejects nearly every real edit in this repository, which would
# ship a fast path that never fires while looking implemented. The bytes that can
# actually change this decision are the ones in `file_path`, and those are gated
# twice: by jq above and by `case` below.
_trw_fast_target() {
  _trw_fp_rel=""
  [ -n "$_trw_enrolled_root" ] || return 1
  command -v jq >/dev/null 2>&1 || return 1
  _trw_fp_raw=$(printf '%s' "$_payload" | jq -r '
      if ((.tool_name // "") == "MultiEdit" or (.tool_name // "") == "NotebookEdit")
      then empty
      else (.tool_input.file_path // empty) end
      | select(type == "string")
      | select(test("^[!-~]+$"))
      | select(index("\"") == null)
      | select(index("\\") == null)
    ' 2>/dev/null) || return 1
  [ -n "$_trw_fp_raw" ] || return 1
  case "$_trw_fp_raw" in *"$_trw_nl"*) return 1 ;; esac

  # NORMALIZATION (FR04). Pure lexical resolution against the root the marker was
  # found in -- never `realpath`, never a symlink walk. Anything that survives is
  # an already-clean repo-relative path, which is the only shape whose POSIX
  # `case` match provably agrees with what resolve_under_root()/fnmatchcase would
  # have compared. Everything else defers: `..`, an absolute path outside the
  # root, a trailing or doubled slash, a residual `./` segment.
  _trw_fp_root="${_trw_enrolled_root%/}"
  case "$_trw_fp_raw" in
    "$_trw_fp_root"/*) _trw_fp_rel="${_trw_fp_raw#"$_trw_fp_root"/}" ;;
    /*) return 1 ;;
    ./*) _trw_fp_rel="${_trw_fp_raw#./}" ;;
    *) _trw_fp_rel="$_trw_fp_raw" ;;
  esac
  case "$_trw_fp_rel" in
    '' | . | .. | /* | */ | ../* | */../* | */.. | ./* | */./* | *//*) return 1 ;;
  esac

  # ALIAS SHAPES. _anchors.py falls back to (st_dev, st_ino) identity whenever the
  # target is a symlink OR has more than one link, so a second name for an
  # anchored file is caught there even though its own path matches no anchor
  # (probe findings N1/N2). The shell cannot compare inodes cheaply, so it
  # refuses to decide either shape. `ls -ld`'s second field is the link count in
  # the POSIX-specified long format, and an unreadable or missing `ls` leaves it
  # unset, which defers. A path that does not exist yet (a Write creating a new
  # file) has no inode to alias and needs neither test.
  #
  # The TOCTOU window between these tests and the match below can only cost an
  # avoidable Python spawn: check_write/post_edit_check re-resolve the path under
  # no-follow, root-confined semantics on every invocation that reaches them, so
  # a race never converts a would-BLOCK into an ALLOW.
  _trw_fp_abs="$_trw_fp_root/$_trw_fp_rel"
  if [ -L "$_trw_fp_abs" ]; then
    return 1
  fi
  if [ -e "$_trw_fp_abs" ]; then
    set -- $(ls -ld -- "$_trw_fp_abs" 2>/dev/null)
    [ "${2:-}" = "1" ] || return 1
  fi
  return 0
}

# _trw_sidecar_clears: 0 only when the sidecar PROVES that no eligible claim
# anchors $_trw_fp_rel and that nothing the enrollment marker attests to has
# changed since the sidecar was written. Every other outcome defers.
#
# THREE independent bindings, because they catch different failures:
#   * the trailing `sha256:` line must equal the marker's own
#     expected_contract_digest -- a sidecar left over from a DIFFERENT contract
#     names a digest the live marker does not, so it is refused;
#   * every artifact the marker's three digests cover is carried as a g1/g0
#     freshness line, and any of them being NEWER than the sidecar defers. This
#     is what stops the fast path from outliving its enrollment: an edited
#     contract (a new anchor this file has never heard of), a resynced hook, or a
#     rewritten pre-commit registration each make the marker `stale`, which the
#     Python path fails CLOSED on -- so without this check the shell would have
#     answered ALLOW where Python answers BLOCK;
#   * the sidecar must not be NEWER than the marker. Enrollment writes the
#     sidecar first and stamps the marker after it, so a sidecar rewritten on its
#     own -- the one shape the digest line cannot catch, since that digest is
#     COPIED from the marker rather than derived from this file's content -- is
#     visibly out of order and defers. A speed bump rather than a boundary: an
#     actor who can rewrite this file can also `touch` the marker, and can
#     equally rewrite THIS library, which is sourced into the deciding shell (see
#     TRUST BOUNDARY above). The durable answers to that actor are unchanged --
#     the marker's digests, the git-side pre-commit/pre-push checks, and the fact
#     that the sidecar is a TRACKED file whose forged copy shows up in
#     `git status` exactly as a tampered hook does.
#
# The marker is parsed with `read` and `case` alone -- no fork, no YAML library --
# the same way session-start.sh reads simple scalar fields. It is written by
# _write_marker(), which emits `schema_version: <int>` first and single-quoted
# scalars after; a marker of any other shape, schema or spelling defers.
_trw_sidecar_clears() {
  _sc_file="$_trw_fp_root/$_trw_globs"
  _sc_marker="$_trw_fp_root/$_trw_marker"
  [ -f "$_sc_file" ] || return 1
  [ -f "$_sc_marker" ] || return 1
  if [ "$_sc_file" -nt "$_sc_marker" ]; then
    return 1
  fi

  _sc_schema=""
  _sc_digest=""
  while IFS= read -r _sc_line || [ -n "$_sc_line" ]; do
    case "$_sc_line" in
      "schema_version: 1") _sc_schema=1 ;;
      "expected_contract_digest: '"*)
        _sc_digest="${_sc_line#expected_contract_digest: \'}"
        _sc_digest="${_sc_digest%\'}"
        ;;
    esac
  done < "$_sc_marker"
  [ "$_sc_schema" = "1" ] || return 1
  [ -n "$_sc_digest" ] || return 1

  _sc_seen=0
  while IFS= read -r _sc_line || [ -n "$_sc_line" ]; do
    # The digest line is the LAST line. Anything after it -- appended patterns,
    # an appended second digest -- means this file is not the artifact the writer
    # produced, so it is not read as one.
    if [ "$_sc_seen" = "1" ]; then
      return 1
    fi
    case "$_sc_line" in
      '' | '#'*) ;;
      'p '*)
        _sc_pat="${_sc_line#p }"
        # Unquoted on purpose: this is the one place the rendered anchor is
        # interpreted AS a pattern. A hit is not a block -- it is a deferral to
        # the entry point that decides claims.
        case "$_trw_fp_rel" in
          $_sc_pat) return 1 ;;
        esac
        ;;
      'g1 '*)
        _sc_art="$_trw_fp_root/${_sc_line#g1 }"
        [ -f "$_sc_art" ] || return 1
        if [ "$_sc_art" -nt "$_sc_file" ]; then
          return 1
        fi
        ;;
      'g0 '*)
        _sc_art="$_trw_fp_root/${_sc_line#g0 }"
        if [ -e "$_sc_art" ]; then
          return 1
        fi
        ;;
      'sha256:'*)
        [ "${_sc_line#sha256:}" = "$_sc_digest" ] || return 1
        _sc_seen=1
        ;;
      *) return 1 ;;
    esac
  done < "$_sc_file"
  [ "$_sc_seen" = "1" ] || return 1
  return 0
}

# --- interpreter resolution ---------------------------------------------------
# `command -v python3` only proves SOME interpreter answers to that name on
# PATH -- never that it has trw_mcp installed. A shell whose PATH puts a
# foreign project's venv first (observed live, 2026-09-03: an unrelated repo's
# .venv/bin/python3, no trw_mcp installed) made `python3 -m trw_mcp....`
# raise ModuleNotFoundError for every enrolled Edit/Write, and because this
# guard is fail-closed once enrolled, that blocked the entire repo for one
# agent -- with a message ("python3 is unavailable") that named neither the
# interpreter it tried nor how to point at the right one.
#
# _trw_project_root: the project root the resolver tiers look under. Claude Code
# exports CLAUDE_PROJECT_DIR; other clients (and a hook run from a subdirectory)
# do not, and $PWD would miss the root's .venv and .mcp.json, so fall back to the
# git top level before settling for the working directory.
_trw_project_root() {
  if [ -n "${CLAUDE_PROJECT_DIR:-}" ]; then
    printf '%s' "$CLAUDE_PROJECT_DIR"
  elif _trw_pr_top=$(git rev-parse --show-toplevel 2>/dev/null) && [ -n "$_trw_pr_top" ]; then
    printf '%s' "$_trw_pr_top"
  else
    printf '%s' "$PWD"
  fi
}

# _trw_mcp_json_interpreter: the interpreter behind .mcp.json's `trw` entry,
# resolved via the launcher's own shebang. The entry itself is deliberately
# portable ("trw-mcp" resolved through PATH, or bare "python3" -- PRD-SEC-006 /
# `_trw_mcp_server_entry`) rather than a machine-absolute interpreter path, so
# a bare "python3"/"python" command carries no information the PATH fallback
# below does not already try; only an absolute or PATH-resolvable launcher
# script is worth reading the shebang of.
_trw_mcp_json_interpreter() {
  _tmi_root=$(_trw_project_root)
  _tmi_json="$_tmi_root/.mcp.json"
  [ -f "$_tmi_json" ] || return 1
  command -v jq >/dev/null 2>&1 || return 1
  _tmi_launcher=$(jq -r '.mcpServers.trw.command // empty' "$_tmi_json" 2>/dev/null) || return 1
  case "$_tmi_launcher" in
    '' | python3 | python) return 1 ;;
    */*) _tmi_path="$_tmi_launcher" ;;
    *) _tmi_path=$(command -v "$_tmi_launcher" 2>/dev/null) || return 1 ;;
  esac
  [ -r "$_tmi_path" ] || return 1
  _tmi_shebang=$(head -n 1 "$_tmi_path" 2>/dev/null) || return 1
  case "$_tmi_shebang" in
    '#!'*) ;;
    *) return 1 ;;
  esac
  set -- ${_tmi_shebang#\#!}
  _tmi_bin="$1"
  case "${_tmi_bin##*/}" in
    env) [ -n "$2" ] && printf '%s' "$2" || return 1 ;;
    *) printf '%s' "$_tmi_bin" ;;
  esac
}

# _trw_resolve_and_run_python: try candidate interpreters, in priority order,
# for the one that actually runs $_trw_module. It resolves and executes in the
# SAME step rather than probing importability separately (a second `-c
# 'import trw_mcp'` per candidate would double the python spawns on the
# common, working path and risk the tight pre-write budget for no benefit):
# `-m` on a module that cannot import fails BEFORE $_trw_module's own
# except-Exception boundary (see check_write.py / post_edit_check.py `main`),
# so it is always exit 1 -- verified, and distinct from every genuine answer
# the entry point can give (0=ALLOW, 2=BLOCK) or the shell's own 124=timeout.
# So a candidate is abandoned only on 1 (import/runtime start failure), 126
# (found but not executable), or 127 (not found); any other code is a real
# answer and ends the search, leaving it in $_rc for the caller.
#
# Order: $TRW_PYTHON (the explicit operator override) -> the project's own
# venv, resolved from $CLAUDE_PROJECT_DIR (what every bundled install actually
# has trw_mcp installed into) -> the interpreter the installer wired into
# .mcp.json's `trw` entry -> PATH `python3` last, matching the fallback
# `_trw_mcp_server_entry` itself uses when no console script is on PATH.
_trw_resolve_and_run_python() {
  _rc=1
  set --
  [ -n "${TRW_PYTHON:-}" ] && set -- "$@" "$TRW_PYTHON"
  _trw_rrp_root=$(_trw_project_root)
  if [ -n "$_trw_rrp_root" ]; then
    set -- "$@" "$_trw_rrp_root/.venv/bin/python" "$_trw_rrp_root/.venv/bin/python3"
  fi
  _trw_rrp_launcher=$(_trw_mcp_json_interpreter) || _trw_rrp_launcher=""
  [ -n "$_trw_rrp_launcher" ] && set -- "$@" "$_trw_rrp_launcher"
  set -- "$@" python3

  for _trw_rrp_cand in "$@"; do
    [ -n "$_trw_rrp_cand" ] || continue
    case "$_trw_rrp_cand" in
      */*) [ -x "$_trw_rrp_cand" ] || continue ;;
      # A bare name (only the final PATH-fallback tier reaches here) is resolved
      # to its absolute path BEFORE it is tried, so a diagnostic naming it points
      # at the exact interpreter PATH picked -- e.g. a shadowing foreign venv --
      # rather than the uninformative literal "python3" every candidate shares.
      *)
        _trw_rrp_resolved=$(command -v "$_trw_rrp_cand" 2>/dev/null) || continue
        _trw_rrp_cand="$_trw_rrp_resolved"
        ;;
    esac
    _trw_python_tried="$_trw_python_tried $_trw_rrp_cand"
    _rc=0
    if command -v timeout >/dev/null 2>&1; then
      printf '%s' "$_payload" | timeout "${_trw_budget}s" "$_trw_rrp_cand" -m "$_trw_module" || _rc=$?
    else
      printf '%s' "$_payload" | "$_trw_rrp_cand" -m "$_trw_module" || _rc=$?
    fi
    case "$_rc" in
      1 | 126 | 127) continue ;;
      *) return 0 ;;
    esac
  done
  return 1
}

# PRD-CORE-265-FR10: ownership advisory. WARNS, never blocks, and can never
# change this hook's exit status -- the whole body is `|| true` and the module it
# runs exits 0 unconditionally. `block` is deliberately absent from the knob
# vocabulary (warn|off): PreToolUse delivery is not reliable on every supported
# client, and a gate that fires on some clients and not others teaches agents to
# distrust it, so ownership is ENFORCED at the scoped-commit boundary and only
# SURFACED here.
#
# ACTIVATION IS A FILE TEST, not a config read. With no formation index there is
# nothing to warn about and this pays literally nothing -- no interpreter spawn,
# no config load, no jq -- which is what keeps the pre-write budget intact for
# every session that is not in a formation.
#
# The PAYLOAD is piped rather than a pre-extracted path: the target-path
# extraction, the pin-first identity resolution and the ownership match then all
# live in one typed place, instead of a second shell parser that could disagree
# with the commit boundary about which file is being written.
_TRW_FORMATION_ADVISORY_BUDGET=2
_trw_formation_advisory() {
  # The root is the PROJECT the client is working in, resolved by the guard's own
  # `_trw_project_root` ($CLAUDE_PROJECT_DIR -> git toplevel -> $PWD). NOT
  # `$_trw_enrolled_root`: that is wherever the intent-contract marker happened to
  # be found while walking up from this hook's own path, which in a monorepo of
  # nested checkouts is not necessarily the project holding the formation.
  _trw_fa_root=$(_trw_project_root)
  [ -n "$_trw_fa_root" ] || return 0
  [ -f "$_trw_fa_root/.trw/runtime/formations.json" ] || return 0
  for _trw_fa_cand in "${TRW_PYTHON:-}" "$_trw_fa_root/.venv/bin/python" "$_trw_fa_root/.venv/bin/python3"; do
    [ -n "$_trw_fa_cand" ] || continue
    [ -x "$_trw_fa_cand" ] || continue
    # TRW_PROJECT_ROOT is set PER COMMAND, never exported: this function must
    # leave the deciding shell byte-identical to how it found it. Exporting it
    # would change what the intent contract's own enrollment resolution sees a
    # few lines below, which is how an advisory silently becomes a decision.
    if command -v timeout >/dev/null 2>&1; then
      printf '%s' "$_payload" | TRW_PROJECT_ROOT="${TRW_PROJECT_ROOT:-$_trw_fa_root}" \
        timeout "${_TRW_FORMATION_ADVISORY_BUDGET}s" \
        "$_trw_fa_cand" -m trw_mcp.tools._formation_hook_advisory || true
    else
      printf '%s' "$_payload" | TRW_PROJECT_ROOT="${TRW_PROJECT_ROOT:-$_trw_fa_root}" \
        "$_trw_fa_cand" -m trw_mcp.tools._formation_hook_advisory || true
    fi
    return 0
  done
  return 0
}

# _trw_guard_main: the whole shared control flow, from trap installation to the
# final exit. The caller has already set the six event-specific variables and has
# recognized enrollment from the filesystem; everything from here is identical
# for both events, which is the point of this file.
_trw_guard_main() {
  # The EXIT trap is re-installed rather than assumed: the caller sets an
  # identical one before sourcing this file (so an aborted source still lands on
  # a decided answer), and repeating it here keeps this function correct for any
  # caller and keeps the two spellings visibly the same line.
  trap '[ "$_trw_exit_decided" = "1" ] || { [ "$_trw_enrolled" = "1" ] && exit 2; exit 0; }' EXIT
  trap '_trw_on_signal' HUP INT QUIT TERM

  _trw_started=$(date +%s 2>/dev/null) || _trw_started=0

  _trw_recognize_slow_legs

  # Reading the payload needs the EXTERNAL `cat`, so `|| exit 0` was a second
  # one-line total disarm from the same attacker-writable .trw/runtime/hook-env.sh:
  # `export PATH=/nonexistent` makes `cat` unresolvable, and the enrolled hook
  # exited 0 two lines before its own no-python fail-closed branch could fire
  # (verified 2026-07-25). Unenrolled stays fail-open, as everywhere else.
  if ! _payload=$(cat); then
    if [ "$_trw_enrolled" = "1" ]; then
      _trw_decide 2
      printf 'BLOCKED (intent-contract): the hook payload could not be read, so the enrolled must_not_happen %s cannot run.\n' \
        "$_trw_noun" >&2 || true
      exit 2
    fi
    _trw_decide 0
    exit 0
  fi

  # PRD-CORE-265-FR10: advisory only, placed BEFORE the unenrolled fail-open exit
  # so a project that never enrolled in the intent contract still gets the
  # warning -- intent-contract enrollment and formation membership are unrelated
  # facts, and gating one on the other would make the advisory dead in most
  # repositories. It cannot affect the decision: `|| true`, and the module it
  # runs always exits 0.
  _trw_formation_advisory || true

  # --- fail-open zone: never enrolled is a clean no-op (NFR01 row 2) ---------
  if [ "$_trw_enrolled" = "0" ]; then
    _trw_decide 0
    exit 0
  fi
  # --- protected zone: any unexpected exit below is an intentional block -----
  # Outer wall-clock bound mirrors the hook's SecurityConfig.intent budget field
  # (typed config is the source of truth; the env knob only overrides the shell
  # guard). The caller resolved it into $_trw_budget. `_trw_resolve_and_run_python`
  # both picks the interpreter and runs $_trw_module under it -- see its header
  # for why importability is proven by running the real module rather than a
  # separate probe.
  # PRD-CORE-254: the proven-no-match shortcut, ahead of every interpreter cost
  # and behind every one of its own defers. Identical for both events -- the
  # post-edit hook reads its OWN payload's path against the same static anchor
  # set, so it needs nothing the pre-write hook wrote and no cross-process state
  # (which would add a race and a file write per edit for an answer both hooks
  # already compute independently). It also means a symlink the write ITSELF
  # created is caught: the post-edit `[ -L ]` runs after the write, where the
  # pre-write hook could not have seen it.
  if _trw_fast_target && _trw_sidecar_clears; then
    _trw_decide 0
    _trw_telemetry "$_trw_event_label" "$_trw_matcher" "0:allowed-fast-path"
    exit 0
  fi

  _trw_python_tried=""
  if [ -z "${TRW_PROJECT_ROOT:-}" ] && [ -n "$_trw_enrolled_root" ]; then
    TRW_PROJECT_ROOT="$_trw_enrolled_root"
    export TRW_PROJECT_ROOT
  fi
  if ! _trw_resolve_and_run_python; then
    # Flag FIRST: every statement below may fail, and `set -e` plus the EXIT trap
    # would otherwise convert a recognized-protected block into a silent allow.
    _trw_decide 2
    printf 'BLOCKED (intent-contract): no interpreter with trw_mcp installed could run the enrolled must_not_happen %s. Tried:%s. Point TRW_PYTHON at the interpreter with trw_mcp installed, e.g. TRW_PYTHON=/path/to/.venv/bin/python.\n' \
      "$_trw_noun" "$_trw_python_tried" >&2 || true
    _trw_telemetry "$_trw_event_label" "$_trw_matcher" "2:no-python"
    exit 2
  fi

  if [ "$_rc" -eq 0 ]; then
    # Decide FIRST, then report: telemetry is best-effort and must never be able
    # to rewrite a decided ALLOW either.
    _trw_decide 0
    _trw_telemetry "$_trw_event_label" "$_trw_matcher" "0:allowed"
    exit 0
  fi

  # Flag FIRST — see the no-python branch above. Everything after this point is
  # best-effort reporting and must never be able to change the exit status.
  _trw_decide 2
  if [ "$_rc" -eq 124 ]; then
    printf 'BLOCKED (intent-contract %s): %s exceeded its %ss budget — failing closed.\n' \
      "$_trw_matcher" "$_trw_timeout_subject" "$_trw_budget" >&2 || true
  fi
  _trw_telemetry "$_trw_event_label" "$_trw_matcher" "2:blocked-rc$_rc"
  exit 2
}
