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
# Everything this hook does that its PreToolUse twin also does lives in
# lib-intent-guard.sh (PRD-CORE-250-FR05). The two files used to share 232
# identical lines including four whole function bodies, so a signal-trap or
# timeout fix applied there and missed here was a live divergence. What remains
# below is this event's own vocabulary and nothing else.
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
#     entry point, under the explicit wall-clock budget resolved below — set well
#     under the hook timeout registered in settings.json, so the 124-to-2 path
#     fires first;
#   * lib-trw.sh runs ONLY in a DETACHED subshell (see _trw_telemetry in
#     lib-intent-guard.sh). It used to run in the foreground at two sites with no
#     bound on either, so `trap "" TERM HUP INT QUIT; sleep 120` appended to
#     lib-trw.sh meant the hook could never reach its own `exit 2` no matter how
#     the client killed it (finding F-A, 2026-07-25);
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

# --- this event's vocabulary --------------------------------------------------
_trw_subject="the falsifier check"
_trw_noun="falsifier"
_trw_timeout_subject="falsifier check"
_trw_event_label="PostToolUse:intent-check"
_trw_matcher="post-edit"
_trw_module="trw_mcp.security.intent_contract.post_edit_check"
# Outer wall-clock bound mirrors SecurityConfig.intent.post_edit_hook_budget_seconds
# (typed config is the source of truth; this env knob only overrides the shell guard).
_trw_budget="${TRW_INTENT_POST_EDIT_BUDGET_SECONDS:-5}"

# --- the shared library, and what happens when it is not there ----------------
# `${0%/*}` rather than `$(cd "$(dirname "$0")" && pwd)`: everything ahead of the
# signal trap must stay fork-free, because two command substitutions there are
# what widened the F-C window into the measured 0.2-0.8 ms band. Parameter
# expansion forks nothing.
#
# The library is control-plane and is covered by `expected_hook_digest`
# (HOOK_SUPPORT_FILES), so tampering with it makes enrollment `stale` and the
# Python entry point fails closed — but that check runs DOWNSTREAM of here, so
# the shell must not fall through to the generic fail-open when the library is
# unreadable. See the ordering note below.
_trw_hook_dir_probe="$0"
case "$_trw_hook_dir_probe" in
  */*) _hook_dir="${_trw_hook_dir_probe%/*}" ;;
  *) _hook_dir="." ;;
esac

# Degraded recognition, then the EXIT trap, then the source — in that order.
# `.` of a missing or unparseable file ABORTS a non-interactive shell outright
# (POSIX XCU 2.14), so `if ! . lib; then` does not catch it: the shell is gone
# before the `then`. Measured — with no library present both hooks exited 2 from
# an UNENROLLED project, which is the opposite failure. So the fail-safe state is
# established BEFORE the source instead of being recovered after it, and every
# abort mode (absent, `chmod 000`, truncated, syntax error) lands on the same
# trap. `chmod 000` on a support file was a git-invisible total disarm once
# already (probe finding N6), and this library is worth more to an attacker than
# `lib-trw.sh` was: it is sourced into the shell that decides.
#
# This probe is deliberately builtins-only and has NO walk-up: there is no
# function to call yet and duplicating one here is what FR05 exists to prevent.
# `$CLAUDE_PROJECT_DIR` is set by every client that registers this hook, so the
# first candidate is the project root in practice; the residual is a broken
# install invoked from a subdirectory with that variable unset, which reads as
# unenrolled and stays inert. The full recognizer runs below once the library is
# in hand and can only widen the answer, never narrow it.
for _trw_candidate in "${CLAUDE_PROJECT_DIR:-$PWD}" "$PWD"; do
  if [ -f "$_trw_candidate/$_trw_marker" ] || [ -f "$_trw_candidate/$_trw_evidence" ]; then
    _trw_enrolled=1
  fi
done
trap '[ "$_trw_exit_decided" = "1" ] || { [ "$_trw_enrolled" = "1" ] && exit 2; exit 0; }' EXIT

if [ -r "$_hook_dir/lib-intent-guard.sh" ]; then
  . "$_hook_dir/lib-intent-guard.sh"
else
  if [ "$_trw_enrolled" = "1" ]; then
    _trw_decided_code=2
    _trw_exit_decided=1
    printf 'BLOCKED (intent-contract): the shared guard library could not be read, so the enrolled must_not_happen falsifier cannot run.\n' >&2 || true
    exit 2
  fi
  _trw_decided_code=0
  _trw_exit_decided=1
  exit 0
fi

# --- FILESYSTEM enrollment recognition, before the SIGNAL trap ---------------
# Ordering, not decoration. The EXIT trap fails open while _trw_enrolled is 0,
# the signal trap is installed after it (inside _trw_guard_main, because it
# needs the library's _trw_on_signal), and recognition used to happen after
# BOTH — so a SIGTERM landing in that preamble exited 0 from an enrolled,
# violating project. Measured 12/12 silent allows across the 0.2-0.8 ms band,
# and the F4 signal test signals at 400 ms, so nothing covered it (finding F-C,
# 2026-07-25). This walk needs `[ -f ]` and nothing else, so it stays ahead of
# the signal trap; the cheap no-walk probe above additionally runs ahead of the
# EXIT trap, so even an aborted source lands on the right answer.
if _trw_recognize_fs "${CLAUDE_PROJECT_DIR:-$PWD}" || _trw_recognize_fs "$PWD"; then
  _trw_enrolled=1
fi

_trw_guard_main
