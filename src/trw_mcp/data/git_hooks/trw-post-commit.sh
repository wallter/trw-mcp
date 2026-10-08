#!/bin/sh
# TRW post-commit maintenance.
#
# PRD-CORE-231 FR01 + FR02. Installed to .trw/hooks/trw-post-commit.sh and
# dispatched from a guarded block in .git/hooks/post-commit.
#
# A commit changes two things TRW cares about at once:
#   * HEAD sha  -> invalidates the sha-keyed T2 hint sidecar (FR01). The CC-03
#     PreToolUse hook stays armed but falls back to T1/T0 until it is re-seeded.
#   * the tree  -> can make a learning's assertions/anchors stale (FR02).
# Both run here, which is what bounds stale-claim latency to the sweep cadence
# instead of "whenever someone happens to recall that entry".
#
# NEVER blocks `git commit` (NFR02):
#   * `exit 0` on every path, including missing Python and missing trw-distill.
#   * The work runs in a DETACHED BACKGROUND subshell, so commit latency is
#     unaffected no matter how long the sidecar CLI or the sweep takes.
#   * A repo without an entitlement is a silent no-op — the free-tier fail-open
#     contract, unchanged.
#
# SINGLE-FLIGHT (PRD-INFRA-186): the worker this script launches takes a lock in
# the store's runtime directory and EXITS IMMEDIATELY when another sweep already
# holds it, leaving a pending marker so the running owner performs exactly one
# follow-up pass. Before that, one full-store sweep was launched per commit and
# 24 commits in three hours left 27 concurrent workers running for up to 4h50m
# (sub_lynArGCloVDuWffm). This script still launches unconditionally — deciding
# in sh would mean reimplementing PID liveness and lock recovery here — but the
# deferred path costs one interpreter start and touches no database.
#
# All policy (enabled flag, per-commit file cap, env allowlist, argv assembly,
# single-flight, budget) lives in typed Python: trw_mcp.tools._post_commit. This
# file stays a thin, un-clever wrapper on purpose — no path interpolation into
# shell strings, no cap arithmetic, nothing to get subtly wrong in sh.
#
# POSIX sh compatible. Tested with: dash, sh, bash.

set -e
trap 'exit 0' EXIT

_repo=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
# Git runs post-commit in the committing checkout; inherited project state may name another worktree.
TRW_PROJECT_DIR="$_repo"
export TRW_PROJECT_DIR

# _trw_pc_unavailable: the maintenance cannot run here. Say so LOUDLY -- one line
# on stderr, which `git commit` shows, and one `python_unavailable=1` event in the
# hook log -- then exit 0 (PRD-FIX-156 s2). Before, this path was silent: the
# sweep "ran" under an interpreter that could not import trw_mcp (L-7zca). The
# log line is a fixed constant (no path in it), appended only when no component
# of its path is a symlink.
_trw_pc_unavailable() {
    printf 'trw post-commit: maintenance skipped: %s. Run `trw-mcp update-project` to record the interpreter in .trw/channels/cc03-python.txt.\n' "$1" >&2 || true
    _pc_dir="${_repo}/.trw/context"
    if [ ! -L "${_repo}/.trw" ] && [ ! -L "$_pc_dir" ] && [ ! -L "$_pc_dir/hook-executions.log" ] \
        && mkdir -p "$_pc_dir" 2>/dev/null; then
        printf '%s event=PostCommit matcher=post-commit exit=0 duration=0s python_unavailable=1\n' \
            "$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null)" >> "$_pc_dir/hook-executions.log" 2>/dev/null || true
    fi
    exit 0
}

# --- Resolve Python: a copy of lib-distill-hint.sh's function, which this
# script cannot source (it installs to .trw/hooks, apart from every client lib) ---
_get_python_path() {
    # Usage: _get_python_path <project_dir>
    # PRD-FIX-155: the one interpreter resolution order, byte-identical in every
    # bundled hook that starts Python (a test pins the copies). First hit wins:
    #   1. <project_dir>/.trw/channels/cc03-python.txt, which init-project and
    #      update-project fill with the interpreter that runs trw-mcp
    #   2. the interpreter in the shebang of `command -v trw-mcp`
    #   3. <project_dir>/.venv/bin/python
    #   4. worktree fallback: in a linked git worktree, .trw is per-worktree and
    #      untracked (steps 1 and 3 above never see it), so fall back to the
    #      MAIN worktree's pointer, then its .venv, resolved via
    #      `git rev-parse --git-common-dir` (one cheap call, only reached
    #      here; a git error or non-worktree checkout just falls through)
    #   5. python3 on PATH, which often cannot import trw_mcp (trw-mcp doctor)
    # A pointer counts only as an ABSOLUTE path to an executable regular file: a
    # relative one would resolve against whatever directory the hook runs in.
    _trw_py=$(cat "$1/.trw/channels/cc03-python.txt" 2>/dev/null) || _trw_py=""
    case "$_trw_py" in /*) [ -f "$_trw_py" ] || _trw_py="" ;; *) _trw_py="" ;; esac
    if [ -z "$_trw_py" ] || [ ! -x "$_trw_py" ]; then
        _trw_py=$(command -v trw-mcp 2>/dev/null) || _trw_py=""
        [ -z "$_trw_py" ] || _trw_py=$(head -n 1 "$_trw_py" 2>/dev/null) || _trw_py=""
        _trw_py=${_trw_py#\#!}
        _trw_py=${_trw_py%% *}
        case "${_trw_py##*/}" in python*) ;; *) _trw_py="" ;; esac
        [ -x "$_trw_py" ] || _trw_py="$1/.venv/bin/python"
    fi
    if [ ! -x "$_trw_py" ]; then
        _trw_common=$(git -C "$1" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || _trw_common=""
        if [ -n "$_trw_common" ]; then
            _trw_main=$(dirname "$_trw_common")
            _trw_py=$(cat "$_trw_main/.trw/channels/cc03-python.txt" 2>/dev/null) || _trw_py=""
            case "$_trw_py" in /*) [ -f "$_trw_py" ] || _trw_py="" ;; *) _trw_py="" ;; esac
            [ -x "$_trw_py" ] || _trw_py="$_trw_main/.venv/bin/python"
        fi
    fi
    if [ -x "$_trw_py" ]; then
        printf '%s' "$_trw_py"
    elif command -v python3 >/dev/null 2>&1; then
        printf 'python3'
    else
        return 1
    fi
}
_py=$(_get_python_path "$_repo") || _trw_pc_unavailable "no Python interpreter found"
# Resolve availability without starting Python in the commit's foreground.
[ -x "$_py" ] || command -v "$_py" >/dev/null 2>&1 || _trw_pc_unavailable "no executable Python"

# The repo root reaches Python through the ENVIRONMENT, never through source
# interpolation — a repo path containing quotes or newlines must not be able to
# inject code into the -c program.
_TRW_PROGRAM='
import os
from pathlib import Path

from trw_mcp.tools._post_commit import run_post_commit

run_post_commit(Path(os.environ["TRW_POST_COMMIT_REPO"]))
'

# The triggering commit travels in the environment too. A detached worker can
# start after HEAD has already moved again (that is the whole reason coalescing
# exists), and a sha it resolves for itself then describes a different commit
# than the one that summoned it. `-C "$_repo"` because the hook's cwd is not
# guaranteed. An empty value is fine — Python falls back to `git rev-parse`
# exactly as before.
_head=$(git -C "$_repo" rev-parse HEAD 2>/dev/null) || _head=""

# TRW_POST_COMMIT_SYNC=1 runs in the foreground so a caller can observe the
# receipt deterministically (used by the installer wiring test). Unset — the
# normal path — detaches so `git commit` never waits.
#
# MEMORY_DAEMON_AUTOSTART=false (PRD-CORE-310 FR04): the sweep uses a memory
# daemon that is already serving and never starts one. A commit with no session
# open used to leave a daemon holding the store and a model for its idle window,
# one per HOME a commit ran under (19 leaked from the test suite alone, B71-105).

if [ "${TRW_POST_COMMIT_SYNC:-}" = "1" ]; then
    TRW_POST_COMMIT_REPO="$_repo" \
    TRW_POST_COMMIT_HEAD="$_head" \
    PYTHONDONTWRITEBYTECODE=1 \
    MEMORY_DAEMON_AUTOSTART=false \
    "$_py" -c "$_TRW_PROGRAM" >/dev/null 2>&1 || _trw_pc_unavailable "worker failed to start or run"
else
    (
        TRW_POST_COMMIT_REPO="$_repo" \
        TRW_POST_COMMIT_HEAD="$_head" \
        PYTHONDONTWRITEBYTECODE=1 \
        MEMORY_DAEMON_AUTOSTART=false \
        "$_py" -c "$_TRW_PROGRAM" || _trw_pc_unavailable "worker failed to start or run"
    ) </dev/null >/dev/null 2>&1 &
fi

exit 0
