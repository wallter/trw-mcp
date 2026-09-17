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

_repo="${TRW_PROJECT_DIR:-$(pwd)}"

# --- Resolve Python (same contract as lib-distill-hint.sh) ---
_python_path_file="${_repo}/.trw/channels/cc03-python.txt"
_py=""
if [ -f "$_python_path_file" ]; then
    _candidate=$(cat "$_python_path_file" 2>/dev/null) || _candidate=""
    if [ -n "$_candidate" ] && [ -x "$_candidate" ]; then
        _py="$_candidate"
    fi
fi
if [ -z "$_py" ]; then
    if command -v python3 >/dev/null 2>&1; then
        _py="python3"
    else
        exit 0
    fi
fi

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

if [ "${TRW_POST_COMMIT_SYNC:-}" = "1" ]; then
    TRW_POST_COMMIT_REPO="$_repo" \
    TRW_POST_COMMIT_HEAD="$_head" \
    PYTHONDONTWRITEBYTECODE=1 \
    "$_py" -c "$_TRW_PROGRAM" >/dev/null 2>&1 || true
else
    (
        TRW_POST_COMMIT_REPO="$_repo" \
        TRW_POST_COMMIT_HEAD="$_head" \
        PYTHONDONTWRITEBYTECODE=1 \
        "$_py" -c "$_TRW_PROGRAM" >/dev/null 2>&1
    ) &
fi

exit 0
