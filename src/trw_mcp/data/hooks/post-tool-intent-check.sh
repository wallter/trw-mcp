#!/bin/sh
# PRD-SEC-013-FR07: PostToolUse falsifier check against the ACTUAL post-edit tree.
# Matcher: Write|Edit|MultiEdit
# Exit 2 = surface the violation to the model. Exit 0 = allow.
#
# The exit-2 here is the immediate SURFACE, never assumed to hard-block: the write
# has already happened. HARD enforcement is the open-violation marker the Python
# entry point persists, which the existing deliver gate treats as BLOCK-class
# until the falsifier passes again or an operator mints a break-glass token.
#
# Fail-open BEFORE protection is recognized (the project never opted in).
# Fail-closed AFTER: once enrollment is recognized, every unexpected exit —
# falsifier failure, timeout 124, missing interpreter, import failure — and every
# CATCHABLE termination signal (HUP/INT/QUIT/TERM) maps to the intentional exit 2
# (PRD-SEC-013 R4/NFR01).
#
# NOT covered, said plainly so no reader takes it for a guarantee: SIGKILL cannot
# be trapped by any process, and a client that enforces its own hook timeout by
# killing this process reports whatever code IT assigns (143 for SIGTERM, 137 for
# SIGKILL) — and only exit 2 blocks. The defence against that is budget headroom
# rather than a trap, so the budget claim has to be true of EVERY foreground step:
#
#   * once enrollment is recognized, the only foreground work left is the python
#     entry point, under the explicit wall-clock budget near the bottom of this
#     file — set well under the hook timeout registered in settings.json, so the
#     124-to-2 path fires first;
#   * the shared lib runs ONLY in a DETACHED subshell (see _trw_telemetry). It
#     used to run in the foreground at two sites with no bound on either, so
#     `trap "" TERM HUP INT QUIT; sleep 120` appended to lib-trw.sh meant the hook
#     could never reach its own `exit 2` no matter how the client killed it, and
#     the telemetry site stranded a BLOCK that had already been decided (finding
#     F-A, 2026-07-25);
#   * the git legs of enrollment recognition are external commands with no bound,
#     and that is deliberate: they only run while the project is still
#     unrecognized, where the correct answer is already exit 0, so a git that
#     hangs costs latency in a project this hook was never going to block.
#
# The trap distinguishes an exit we DECIDED from one that merely happened. An
# undecided exit after enrollment is recognized is not evidence of safety: it is
# an abort we cannot attribute, so it maps to 2. Before recognition it is the
# generic fail-open.
set -e
_trw_exit_decided=0
_trw_decided_code=0
_trw_enrolled=0
_trw_marker=".trw/contracts/enrollment.yaml"
_trw_evidence=".trw/intent-enrollment-evidence.yaml"

# --- FILESYSTEM enrollment recognition, BEFORE any trap ----------------------
# Ordering, not decoration. The EXIT trap below fails open while _trw_enrolled is
# 0, the signal trap is installed after it, and recognition used to happen after
# BOTH — so a SIGTERM landing in that preamble exited 0 from an enrolled,
# violating project. Measured 12/12 silent allows across the 0.2-0.8 ms band, and
# the F4 signal test signals at 400 ms, so nothing covered it (finding F-C,
# 2026-07-25). This leg needs `[ -f ]` and nothing else, so it can precede the
# traps entirely; what remains ahead of it is shell builtins only.
#
# `$PWD` and `$CLAUDE_PROJECT_DIR` are both consulted, and each is walked upward,
# because the git-derived root is NOT trustworthy for this question: a stub whose
# `--show-toplevel` prints an empty directory pointed both `[ -f ]` tests at the
# wrong tree and disarmed the hook (finding F-G). Adding candidate roots can only
# ever turn recognition ON, so a lying git can no longer turn it off.
_trw_recognize_fs() {
  _trw_scan="$1"
  while [ -n "$_trw_scan" ] && [ "$_trw_scan" != "/" ]; do
    if [ -f "$_trw_scan/$_trw_marker" ] || [ -f "$_trw_scan/$_trw_evidence" ]; then
      return 0
    fi
    case "$_trw_scan" in
      */*) _trw_scan="${_trw_scan%/*}" ;;
      *) return 1 ;;
    esac
  done
  return 1
}
if _trw_recognize_fs "${CLAUDE_PROJECT_DIR:-$PWD}" || _trw_recognize_fs "$PWD"; then
  _trw_enrolled=1
fi

trap '[ "$_trw_exit_decided" = "1" ] || { [ "$_trw_enrolled" = "1" ] && exit 2; exit 0; }' EXIT

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
    printf 'BLOCKED (intent-contract): the falsifier check was terminated by a signal before it could decide.\n' >&2 || true
    exit 2
  fi
  _trw_decide 0
  exit 0
}
trap '_trw_on_signal' HUP INT QUIT TERM

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
_trw_started=$(date +%s 2>/dev/null) || _trw_started=0

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
# So the lib is now reduced to what it is actually for here — best-effort
# telemetry — and that runs DETACHED. A backgrounded subshell cannot write this
# shell's variables, cannot define functions here, its `exit` ends itself, and it
# cannot hold the decision open for even one scheduler tick. Its three standard
# descriptors are redirected so it can never hold the client's pipe open either.
# `init_hook_timer` is inlined as `date +%s` above so the duration stays real.
#
# The probe that used to read HOOKS_ENABLED out of the lib is GONE rather than
# bounded, because it never had a live output: HOOKS_ENABLED was consulted only
# when `_trw_enrolled=0`, and an unenrolled project exits 0 a few lines further
# down regardless. Its failure branch — block when the lib cannot be sourced —
# was redundant too: `expected_hook_digest` covers lib-trw.sh, so ANY tamper
# (content or mode) makes enrollment `stale`, which the python entry point fails
# closed on. The hook no longer depends on the lib to decide anything.
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
# This hook is registered unconditionally on every installed project, so that
# ruling blocked every Write/Edit for users who never opted in. What it was
# defending is preserved by the filesystem signal.
#
# RESIDUAL: deleting the evidence file AND the marker AND keeping git from
# answering disarms this. That is three acts rather than two, the evidence file
# is git-tracked (a committed removal is a C9 finding) and it lives outside
# .trw/contracts, so `rm -rf .trw/contracts` does not take it. The risk in the
# other direction — blocking every write for every never-enrolled user with a
# typo in their gitconfig — is both larger and certain.
if [ "$_trw_enrolled" = "0" ]; then
  # `$PWD` is required to name the real cwd at shell startup and both /bin/sh and
  # dash recompute a stale inherited value — but `pwd` is the definition rather
  # than a cached copy of it, and re-walking from it costs one fork in a project
  # we have not recognized yet. It runs HERE, after the traps, because a fork
  # before them would re-open the window finding F-C closed.
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
    elif git -C "$_trw_root" ls-files --error-unmatch "$_trw_marker" >/dev/null 2>&1; then
      _trw_enrolled=1
    fi
  fi
fi

# Reading the payload needs the EXTERNAL `cat`, so `|| exit 0` was a second
# one-line total disarm from the same attacker-writable .trw/runtime/hook-env.sh:
# `export PATH=/nonexistent` makes `cat` unresolvable, and the enrolled hook
# exited 0 two lines before its own no-python fail-closed branch could fire
# (verified 2026-07-25). Unenrolled stays fail-open, as everywhere else.
if ! _payload=$(cat); then
  if [ "$_trw_enrolled" = "1" ]; then
    _trw_decide 2
    printf 'BLOCKED (intent-contract): the hook payload could not be read, so the enrolled must_not_happen falsifier cannot run.\n' >&2 || true
    exit 2
  fi
  _trw_decide 0
  exit 0
fi

# --- fail-open zone: never enrolled is a clean no-op (NFR01 row 2) -----------
if [ "$_trw_enrolled" = "0" ]; then
  _trw_decide 0
  exit 0
fi
command -v python3 >/dev/null 2>&1 || {
  # Flag FIRST: every statement below may fail, and `set -e` plus the EXIT trap
  # would otherwise convert a recognized-protected block into a silent allow.
  _trw_decide 2
  printf 'BLOCKED (intent-contract): python3 is unavailable, so the enrolled must_not_happen falsifier cannot run.\n' >&2 || true
  _trw_telemetry "PostToolUse:intent-check" "post-edit" "2:no-python"
  exit 2
}

# --- protected zone: any unexpected exit below is an intentional block -------
# Outer wall-clock bound mirrors SecurityConfig.intent.post_edit_hook_budget_seconds
# (typed config is the source of truth; this env knob only overrides the shell guard).
_budget="${TRW_INTENT_POST_EDIT_BUDGET_SECONDS:-5}"
_rc=0
if command -v timeout >/dev/null 2>&1; then
  printf '%s' "$_payload" | timeout "${_budget}s" python3 -m trw_mcp.security.intent_contract.post_edit_check || _rc=$?
else
  printf '%s' "$_payload" | python3 -m trw_mcp.security.intent_contract.post_edit_check || _rc=$?
fi

if [ "$_rc" -eq 0 ]; then
  # Decide FIRST, then report: telemetry is best-effort and must never be able to
  # rewrite a decided ALLOW either.
  _trw_decide 0
  _trw_telemetry "PostToolUse:intent-check" "post-edit" "0:allowed"
  exit 0
fi

# Flag FIRST — see the no-python branch above. Everything after this point is
# best-effort reporting and must never be able to change the exit status.
_trw_decide 2
if [ "$_rc" -eq 124 ]; then
  printf 'BLOCKED (intent-contract post-edit): falsifier check exceeded its %ss budget — failing closed.\n' "$_budget" >&2 || true
fi
_trw_telemetry "PostToolUse:intent-check" "post-edit" "2:blocked-rc$_rc"
exit 2
