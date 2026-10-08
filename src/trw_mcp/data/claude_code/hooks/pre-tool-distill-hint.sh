#!/bin/sh
# CC-03 PreToolUse hook: trw-distill edit hint.
#
# PRD-DIST-2405 FR25-FR32.
#
# Reads PreToolUse JSON from stdin and emits an advisory hint on stdout.
# NEVER exits 2 under any condition (FR26) — always advisory, never blocking.
# Default state: opt-in (exits 0 silently unless cc03_hook_enabled: true).
#
# POSIX sh compatible (NFR08). Tested with: dash, sh, bash.
#
# Skip conditions (all exit 0 silently) — FR27:
#   1. cc03_hook_enabled: false (default opt-in gate)
#   2. file_path resolves outside repo root
#   3. agent_type in {trw-distill-explorer, Explore, Plan}
#   4. file extension in safe-skip allowlist
#   5. same file_path hinted within last 180 seconds (debounce)
#   6. Python import fails AND no learnings match
#
# Threat model. TRUSTED: the session's own environment (inherited PYTHONPATH,
# CLAUDE_PROJECT_DIR, TRW_PROJECT_DIR are the operator's) and the session
# repository's `.trw/` operator state and tracked hook scripts. NOT trusted:
# anything about where the edited file sits -- its directory, the `.git` beside
# it, what git reports from there, or any `.trw` it carries.
#
# A checkout other than the session root is the session's only when the
# repository's own back-link says so: the main checkout's real `.git` directory,
# or `<common>/worktrees/<n>/gitdir` naming that worktree's `.git` file. A main
# checkout created with --separate-git-dir therefore gets no hint when edited
# from one of its worktrees' sessions (accepted).
#
# Latency. Every process this hook waits for is bounded, and each expiry is a
# silent exit 0 (FR30):
#   payload parse   ONE jq/python3 start        _TRW_PARSE_BUDGET_S  0.5s  (KILL)
#   shell git       all calls share one budget  _TRW_GIT_BUDGET_S    0.7s  (KILL)
#   hint program    _trw_bounded_python + its own SIGALRM            2.5s  (TERM, KILL 0.2s later;
#                   where the box has a `timeout` binary, that binary's TERM alone)
#   one tail start  timeout re-stamp, or the JSON emit  _TRW_TAIL_BOUND_S  0.8s  (same)
# Worst case 0.5 + 0.7 + 2.5 + 0.8 = 4.5s plus kill grace and process starts,
# against the 5s the installer registers. The parse and tail starts normally
# take 10-30ms: their bounds leave a loaded machine 25x or more, so ordinary
# load does not lose a hint (20/20 under an 18-process CPU load, 2026-10-08). Only the DIRECT child is killed: a
# wrapper script's own children outlive the hook until they finish by
# themselves (real git, jq and python are single processes). Without mktemp
# the parse and git bounds do not apply.

set -e
trap 'exit 0' EXIT

# git answers for the directory it is pointed at, never for a repository or a
# search ceiling an inherited environment names. Before any library is sourced.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_CEILING_DIRECTORIES 2>/dev/null || true

_hook_dir="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib-distill-hint.sh
. "$_hook_dir/lib-distill-hint.sh" 2>/dev/null || exit 0

# --- Extract every payload field in ONE bounded parser start (FR25) -------------
# jq, else python3, never a shell parser (T29); a host with neither emits
# nothing. Six separate starts used to run here with no bound at all: a parser
# that hung held the hook for as long as it liked. The parser reads the hook's
# stdin directly and prints one field per line:
#   ok|bad, tool_use_id, file_path, tool_name, session_id, agent_name, cwd,
#   then one line per "*** Update File:" / "*** Add File:" target of a Codex
#   apply_patch command (PRD-CORE-336-FR04), deduped in first-seen order.
# A field that is not a string, or holds a newline, prints empty; a file_path
# holding a newline prints "bad" (it would otherwise be read as two files and
# the hint would be for files nobody is editing) and the hook stays silent.
_tool_use_id=""
_file_path=""
_tool_name=""
_agent_type=""

. "$_hook_dir/lib-trw.sh" 2>/dev/null || exit 0

_TRW_PARSE_BUDGET_S="${TRW_CC03_PARSE_BUDGET_S:-0.5}"
_TRW_GIT_BUDGET_S="${TRW_CC03_GIT_BUDGET_S:-0.7}"
_TRW_TAIL_BOUND_S="${TRW_CC03_TAIL_BOUND_S:-0.8}"
# The hook's own scratch directory in $TMPDIR (parser output, the git budget's
# pid and stdout): never checkout state. Removed by _trw_hook_cleanup.
_trw_tmp=$(mktemp -d 2>/dev/null) || _trw_tmp=""
_trw_git_dir=""
_trw_git_timer=""
_trw_hook_cleanup() {
    if [ -n "$_trw_git_timer" ]; then
        kill -TERM "$_trw_git_timer" 2>/dev/null || true
        # Reap it, quietly: an unreaped killed job makes bash print "Terminated: 15" on the hook's stderr.
        wait "$_trw_git_timer" 2>/dev/null || true
    fi
    [ -z "$_trw_tmp" ] || rm -rf "$_trw_tmp" 2>/dev/null || true
    [ -z "${_remedy_receipt:-}" ] || rm -f "$_remedy_receipt" 2>/dev/null || true
}
trap '_trw_hook_cleanup; exit 0' EXIT

_TRW_PARSE_JQ='
def s: if type == "string" and (test("[\n\r]") | not) then . else "" end;
(if type == "object" then . else error("payload is not an object") end) as $d
| ($d.tool_input | if type == "object" then . else {} end) as $ti
| (if ($ti.file_path | type) == "string" and ($ti.file_path | test("[\n\r]")) then "bad" else "ok" end),
  ($d.tool_use_id | s), ($ti.file_path | s), ($d.tool_name | s), ($d.session_id | s), ($d.agent_name | s), ($d.cwd | s),
  (($ti.command | if type == "string" then . else "" end)
   | [split("\n")[] | select(test("^\\*\\*\\* (Update|Add) File: .*[^ \t]")) | sub("^\\*\\*\\* (Update|Add) File: "; "")]
   | reduce .[] as $x ([]; if index($x) then . else . + [$x] end) | .[])
'
_TRW_PARSE_PY='
import json, sys
try:
    d = json.load(sys.stdin.buffer)
    if not isinstance(d, dict):
        raise ValueError("payload is not an object")
except Exception:
    sys.exit(1)
ti = d.get("tool_input")
ti = ti if isinstance(ti, dict) else {}
def multi(v):
    return isinstance(v, str) and ("\n" in v or "\r" in v)
def s(v):
    return v if isinstance(v, str) and not multi(v) else ""
fp = ti.get("file_path")
out = ["bad" if multi(fp) else "ok", s(d.get("tool_use_id")), s(fp), s(d.get("tool_name")), s(d.get("session_id")), s(d.get("agent_name")), s(d.get("cwd"))]
cmd = ti.get("command")
for line in cmd.split("\n") if isinstance(cmd, str) else []:
    for prefix in ("*** Update File: ", "*** Add File: "):
        name = line[len(prefix):]
        if line.startswith(prefix) and name.strip() and name not in out[7:]:
            out.append(name)
sys.stdout.write("\n".join(out) + "\n")
'
if command -v jq >/dev/null 2>&1; then
    set -- jq -r "$_TRW_PARSE_JQ"
elif command -v python3 >/dev/null 2>&1; then
    set -- python3 -I -S -c "$_TRW_PARSE_PY"
else
    exit 0
fi
if [ -n "$_trw_tmp" ]; then
    exec 3<&0
    "$@" <&3 > "$_trw_tmp/payload" 2>/dev/null &
    _parse_pid=$!
    ( sleep "$_TRW_PARSE_BUDGET_S"; kill -KILL "$_parse_pid" ) </dev/null >/dev/null 2>&1 &
    _parse_watch=$!
    _parse_rc=0
    wait "$_parse_pid" 2>/dev/null || _parse_rc=$?
    kill -TERM "$_parse_watch" 2>/dev/null || true
    wait "$_parse_watch" 2>/dev/null || true
    exec 3<&-
    [ "$_parse_rc" -eq 0 ] || exit 0
    _parsed=$(cat "$_trw_tmp/payload" 2>/dev/null) || exit 0
else
    _parsed=$("$@" 2>/dev/null) || exit 0
fi
_parse_status=""
_session_id=""
_payload_cwd=""
_patch_targets=""
{
    IFS= read -r _parse_status || true
    IFS= read -r _tool_use_id || true
    IFS= read -r _file_path || true
    IFS= read -r _tool_name || true
    IFS= read -r _session_id || true
    IFS= read -r _agent_type || true
    IFS= read -r _payload_cwd || true
    while IFS= read -r _patch_line; do
        _patch_targets="${_patch_targets}${_patch_targets:+
}${_patch_line}"
    done
} <<EOF_PARSED
$_parsed
EOF_PARSED
[ "$_parse_status" = "ok" ] || exit 0
_session_id="${_session_id:-${TRW_SESSION_ID:-${CLAUDE_SESSION_ID:-}}}"

# _candidates holds one path per line: the single file_path for Claude Code,
# the patch targets for Codex.
_candidates="$_file_path"
if [ -z "$_file_path" ] && [ "$_tool_name" = "apply_patch" ]; then
    _candidates="$_patch_targets"
fi

# --- One budget for every shell git call ----------------------------------------
# `git` below is this function: each call (here and inside the library, e.g.
# _get_python_path) runs in the background under one shared timer. When the
# timer fires it marks the budget expired and kills the call in flight; that
# call and every later one return 124, and the hook exits 0 silently. Armed in
# the MAIN shell (a `$(...)` subshell could not share it), lazily, so the common
# path -- which asks git nothing -- pays for none of it. Without mktemp the
# calls run unbounded, as before.
_trw_git_arm() {
    [ -z "$_trw_git_timer" ] || return 0
    _trw_git_dir="$_trw_tmp"
    [ -n "$_trw_git_dir" ] || return 0
    (
        sleep "$_TRW_GIT_BUDGET_S"
        : > "$_trw_git_dir/expired"
        _tga_pid=$(cat "$_trw_git_dir/pid" 2>/dev/null) || _tga_pid=""
        # KILL, not TERM: these probes only read, and a git that ignores TERM would
        # otherwise hold the `wait` below for as long as it runs.
        [ -z "$_tga_pid" ] || kill -KILL "$_tga_pid"
    ) </dev/null >/dev/null 2>&1 &
    _trw_git_timer=$!
}
_trw_git_expired() {
    [ -n "$_trw_git_dir" ] && [ -e "$_trw_git_dir/expired" ]
}
git() {
    if [ -z "$_trw_git_dir" ]; then
        command git "$@"
        return
    fi
    ! _trw_git_expired || return 124
    command git "$@" > "$_trw_git_dir/out" 2>/dev/null &
    _tg_pid=$!
    printf '%s' "$_tg_pid" > "$_trw_git_dir/pid"
    _tg_rc=0
    wait "$_tg_pid" 2>/dev/null || _tg_rc=$?
    : > "$_trw_git_dir/pid"
    ! _trw_git_expired || return 124
    cat "$_trw_git_dir/out" 2>/dev/null
    return $_tg_rc
}
# The directory holding the nearest `.git` entry at or above a path, as a
# physical path; nothing when there is none. Pure shell: no process is started,
# and no question is put to git, whose answer a hostile directory can shape.
# Args: $1 = absolute path (it need not exist yet).
_trw_nearest_checkout() {
    _nc_d=${1%/*}
    [ -n "$_nc_d" ] || _nc_d=/
    while [ ! -d "$_nc_d" ] && [ "$_nc_d" != / ]; do
        _nc_d=${_nc_d%/*}
        [ -n "$_nc_d" ] || _nc_d=/
    done
    _nc_d=$(cd "$_nc_d" 2>/dev/null && pwd -P) || return 1
    while :; do
        if [ -e "$_nc_d/.git" ] || [ -L "$_nc_d/.git" ]; then
            printf '%s' "$_nc_d"
            return 0
        fi
        [ "$_nc_d" != / ] || return 1
        _nc_d=${_nc_d%/*}
        [ -n "$_nc_d" ] || _nc_d=/
    done
}

# --- The session's own repository ---------------------------------------------
# Everything that decides WHETHER and WITH WHAT this hook runs comes from the
# session's project, never from the directory the edited file happens to be in:
# the opt-in gate, the interpreter pointer and the source path. A file in some
# other repository B must not be able to switch the hook on, name the
# interpreter, put B/*/src on PYTHONPATH or receive .trw state (review 2, P0).
_session_cwd="$_payload_cwd"
[ -n "$_session_cwd" ] && [ -d "$_session_cwd" ] || _session_cwd=$(pwd)
_session_root="${TRW_PROJECT_DIR:-${CLAUDE_PROJECT_DIR:-}}"
if [ -z "$_session_root" ]; then
    # Codex sets neither: the checkout around the session's directory, else that directory.
    _trw_git_arm
    _session_root=$(git -C "$_session_cwd" rev-parse --show-toplevel 2>/dev/null) || _session_root="$_session_cwd"
    ! _trw_git_expired || exit 0
fi
_session_root=$(cd "$_session_root" 2>/dev/null && pwd -P) || exit 0

# --- Skips that need no git call: agent type, extension ------------------------
case "$_agent_type" in
    trw-distill-explorer|Explore|Plan) exit 0 ;;
esac
_any_hintable=0
while IFS= read -r _cand; do
    [ -n "$_cand" ] || continue
    _is_safe_extension "$_cand" || { _any_hintable=1; break; }
done <<EOF_PRECHECK
$_candidates
EOF_PRECHECK
[ "$_any_hintable" -eq 1 ] || exit 0

# --- Skip 1: opt-in gate (FR09) ---
# An explicit true/false in .trw/config.yaml always wins and is read in shell.
# It is the SESSION project's config; a linked worktree that carries no
# .trw/config.yaml of its own (.trw untracked) is governed by its main
# checkout's, so `cc03_hook_enabled: false` there is not lost in a worktree.
# With no explicit setting (auto: on when trw-distill is importable), the
# find_spec probe is NOT a separate interpreter start any more: it rides as the
# first statement of the one bounded Python call below (exit
# $_TRW_CC03_AUTO_OFF_RC = off, stay silent). Measured 2026-09-27: the separate
# probe cost ~60ms of a ~340ms non-compute overhead per edit. Everything between
# here and that call only reads state, so an auto-off hook still has no side
# effects.
_session_common=""
_config_root="$_session_root"
if [ ! -f "$_session_root/.trw/config.yaml" ]; then
    _trw_git_arm
    _session_common=$(git -C "$_session_root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || _session_common=""
    ! _trw_git_expired || exit 0
    case "$_session_common" in
        */.git) [ ! -f "${_session_common%/.git}/.trw/config.yaml" ] || _config_root="${_session_common%/.git}" ;;
    esac
fi
_cc03_probe=0
case "$(TRW_PROJECT_DIR="$_config_root" _cc03_explicit_setting)" in
    on) ;;
    off) exit 0 ;;
    *) _cc03_probe=1 ;;
esac

# --- The checkout the edited file belongs to ----------------------------------
# A session rooted in the main checkout that edits .trw/worktrees/<wt>/x.py must
# be answered from <wt> (its HEAD, its path "x.py"), or it looks up a path
# main's sidecar has never heard of. So the hook re-roots to the file's
# checkout, but ONLY when that checkout is the session's own repository.
#
# Whose a file is, is decided by the nearest `.git` entry above it, found in
# shell before git is asked anything (so a nested repository git is told not to
# see -- a ceiling, a broken gitfile, core.worktree -- is still not ours):
#   - the session root itself: ours, and no git call is made at all;
#   - anywhere else: ours only if it is a REGISTERED worktree of the session's
#     repository. git must report that very directory as the toplevel, the
#     session's common directory, and a git dir that IS that common directory
#     or sits directly under <common>/worktrees/. The last test is the one a
#     forgery cannot pass: a directory can claim any commondir in files of its
#     own, but only the repository writes into its own worktrees/ registry;
#   - none: only a session project that is a TRW project without git.
# Every other place (an unrelated or forged repository, a submodule, a path in
# no repository) gets nothing: no hint, no record, nothing created. A relative
# path is read against the payload's cwd when it names one (a Codex patch is
# relative to its session's directory), else against the session project.
_path_base="$_payload_cwd"
[ -n "$_path_base" ] && [ -d "$_path_base" ] || _path_base="$_session_root"
_first_target=${_candidates%%"
"*}
[ -n "$_first_target" ] || exit 0
case "$_first_target" in /*) ;; *) _first_target="$_path_base/$_first_target" ;; esac
_target_top=$(_trw_nearest_checkout "$_first_target" 2>/dev/null) || _target_top=""
if [ -z "$_target_top" ]; then
    [ -d "$_session_root/.trw" ] || exit 0
    _repo_top="$_session_root"
elif [ "$_target_top" = "$_session_root" ]; then
    _repo_top="$_session_root"
else
    _trw_git_arm
    [ -n "$_session_common" ] || _session_common=$(git -C "$_session_root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || exit 0
    _target_git=$(git -C "$_target_top" rev-parse --path-format=absolute --show-toplevel --git-common-dir --absolute-git-dir 2>/dev/null) || exit 0
    ! _trw_git_expired || exit 0
    _target_seen=$(printf '%s\n' "$_target_git" | sed -n 1p)
    _target_common=$(printf '%s\n' "$_target_git" | sed -n 2p)
    _target_gitdir=$(printf '%s\n' "$_target_git" | sed -n 3p)
    [ -n "$_target_seen" ] && [ -n "$_target_common" ] && [ -n "$_target_gitdir" ] || exit 0
    _target_seen=$(cd "$_target_seen" 2>/dev/null && pwd -P) || exit 0
    _target_common=$(cd "$_target_common" 2>/dev/null && pwd -P) || exit 0
    _target_gitdir=$(cd "$_target_gitdir" 2>/dev/null && pwd -P) || exit 0
    _session_common_real=$(cd "$_session_common" 2>/dev/null && pwd -P) || exit 0
    [ "$_target_seen" = "$_target_top" ] || exit 0
    [ "$_target_common" = "$_session_common_real" ] || exit 0
    # What git reported so far a directory can arrange by POINTING at the
    # session's git directory (a `.git` file or symlink naming <common> or one of
    # its worktrees/<n>). The back-link is what it cannot arrange: it is written
    # by the repository, inside the repository.
    case "$_target_gitdir" in
        "$_session_common_real")
            # "I am the main checkout": then my .git IS that directory, for real.
            [ -d "$_target_top/.git" ] && [ ! -L "$_target_top/.git" ] || exit 0
            _target_dotgit=$(cd "$_target_top/.git" 2>/dev/null && pwd -P) || exit 0
            [ "$_target_dotgit" = "$_session_common_real" ] || exit 0
            ;;
        "$_session_common_real"/worktrees/*/*) exit 0 ;;
        "$_session_common_real"/worktrees/?*)
            # "I am a linked worktree": then worktrees/<n>/gitdir, which git wrote
            # when it registered me, names MY .git file -- a regular file, here.
            [ -f "$_target_top/.git" ] && [ ! -L "$_target_top/.git" ] || exit 0
            _target_backlink=""
            { IFS= read -r _target_backlink < "$_target_gitdir/gitdir"; } 2>/dev/null || true
            case "$_target_backlink" in
                */.git) ;;
                *) exit 0 ;;
            esac
            case "$_target_backlink" in /*) ;; *) _target_backlink="$_target_gitdir/$_target_backlink" ;; esac
            _target_backlink=$(cd "${_target_backlink%/.git}" 2>/dev/null && pwd -P) || exit 0
            [ "$_target_backlink" = "$_target_top" ] || exit 0
            ;;
        *) exit 0 ;;
    esac
    _repo_top="$_target_top"
fi
TRW_PROJECT_DIR="$_repo_top"
export TRW_PROJECT_DIR

# --- Skip 2: no file to hint ---
[ -n "$_candidates" ] || exit 0

# --- Skip 4 + 5, per file: safe extension allowlist (P0-10 fix), then a 180s
# debounce. At most _TRW_MAX_HINT_FILES files are hinted per call, so a large
# patch stays inside the 2.5s budget; files past the cap are neither hinted nor
# debounced. The FIRST surviving file names the CC-04 record.
_TRW_MAX_HINT_FILES=5
_repo="$TRW_PROJECT_DIR"
_debounce_dir="${_repo}/.trw/context/cc03-debounce"

# Sanitized name PLUS a checksum of the exact path. The sanitizer alone is
# lossy -- it deletes every character outside [A-Za-z0-9_.-], so 'src/a.py'
# and 'src/\u03b1.py' both collapse to 'src_.py'-ish forms and the second file
# edited was silently debounced for 180s as if it were the first. cksum is
# POSIX, present everywhere this runs, and costs no interpreter start.
# Args: $1=candidate path. Prints the debounce marker path for it.
_trw_debounce_path() {
    _tdp_safe_name=$(printf '%s' "$1" | tr '/' '_' | tr -cd 'a-zA-Z0-9_.-')
    _tdp_path_ck=$(printf '%s' "$1" | cksum | cut -d' ' -f1)
    printf '%s/%s-%s.ts' "$_debounce_dir" "$_tdp_safe_name" "$_tdp_path_ck"
}

_hint_files=""
_hint_count=0
_file_path=""
while IFS= read -r _cand; do
    [ -n "$_cand" ] || continue
    [ "$_hint_count" -lt "$_TRW_MAX_HINT_FILES" ] || break
    # A target outside the project (scratch files): no sidecar can know it, so no interpreter, no record.
    # Containment is judged on the absolute path (a relative one is read against $_path_base, as the
    # repository resolution above and the Python lookup below read it); $_cand itself stays AS GIVEN,
    # because it names the CC-04 record, the debounce marker and the hint header.
    case "$_cand" in /*) _cand_abs="$_cand" ;; *) _cand_abs="$_path_base/$_cand" ;; esac
    _path_inside_repo "$_cand_abs" "$_repo" || continue
    if [ -e "$_repo/.git" ] || [ -L "$_repo/.git" ]; then
        # Inside the checkout's directory but below some other `.git`: not this checkout's file.
        [ "$(_trw_nearest_checkout "$_cand_abs" 2>/dev/null)" = "$_repo" ] || continue
    fi
    if _is_safe_extension "$_cand"; then
        continue
    fi
    _debounce_file=$(_trw_debounce_path "$_cand")
    # PRD-SEC/RC8: _trw_safe_read treats a symlinked debounce marker as absent
    # and _trw_safe_write refuses to write through one (lib-trw.sh).
    if [ -d "$_debounce_dir" ]; then
        _last=$(_trw_safe_read "$_debounce_file") || _last=0
        if [ -n "$_last" ]; then
            _now=$(date +%s 2>/dev/null) || _now=0
            _diff=$(( _now - _last ))
            if [ "$_diff" -lt 180 ] 2>/dev/null; then
                continue
            fi
        fi
    fi
    # The marker is written AFTER the Python subprocess reports this file as
    # actually attempted (CORE-336-S3-KI), not here: this loop only says the
    # file is a HINT CANDIDATE. The Python side also enforces its own 1.6s
    # start-cutoff (see TRW_CC03_BATCH_BUDGET_S below) so a large batch never
    # trips the 2.4s SIGALRM deadline; a candidate that cutoff skips was never
    # computed, so debouncing it here would silently swallow its next edit too
    # for the full 180s window.
    [ -n "$_file_path" ] || _file_path="$_cand"
    _hint_files="${_hint_files}${_cand}
"
    _hint_count=$(( _hint_count + 1 ))
done <<EOF_CANDIDATES
$_candidates
EOF_CANDIDATES
[ -n "$_file_path" ] || exit 0

# --- Resolve Python path ---
# The interpreter pointer and the source path are the SESSION repository's (its
# main checkout's when a worktree has none): never the edited checkout's.
# _get_python_path asks git only when the session root's own pointer is not usable; arm the budget just then.
_pointer=""
{ IFS= read -r _pointer < "$_session_root/.trw/channels/cc03-python.txt"; } 2>/dev/null || true
case "$_pointer" in /*) [ -f "$_pointer" ] && [ -x "$_pointer" ] || _trw_git_arm ;; *) _trw_git_arm ;; esac
_py=$(_get_python_path "$_session_root" 2>/dev/null) || {
    # Auto mode with no interpreter: trw-distill cannot be importable, so the
    # hook is off (silent), exactly as the old standalone probe decided.
    exit 0
}
! _trw_git_expired || exit 0
_wt_pythonpath=$(_worktree_pythonpath "$_session_root" 2>/dev/null) || _wt_pythonpath=""

# --- Call compute_before_edit_hint via Python subprocess (FR30) ---
# Bounded at 2500ms by _trw_bounded_python; stay silent on failure/timeout.
_hints_dir="${_repo}/.trw/context/cc03-hints"

# One line per file the Python subprocess actually attempted (CORE-336-S3-KI),
# appended as it starts each one -- so a file already attempted before a
# mid-batch timeout or exception still shows up here even though the batch as
# a whole never finishes. Removed below; never trusted across calls.
#
# `mktemp` in the OS temp dir, NEVER a path under $_hints_dir: a checkout can
# ship `.trw/context/cc03-hints` (or an ancestor) as a symlink, and a plain
# `${_hints_dir}/...` path follows that symlink to write attacker-chosen
# content wherever it points, with no race required (PRD-SEC/RC8; caught by
# codex review core336-s3ki r1 of 6fb90b972). A private mktemp'd file is not
# reachable through anything the checkout controls, so this journal never goes
# through `_trw_safe_write`'s checkout-relative symlink checks at all --
# same reasoning as `_bp_out` in `_trw_bounded_python` above.
# Without mktemp (minimal PATHs exist, see lib-trw.sh) the hint still runs: the
# remedy line just cannot be marked as delivered, so it may print again.
_remedy_receipt=$(mktemp 2>/dev/null) || _remedy_receipt=""
_processed_file=$(mktemp 2>/dev/null) || _processed_file=""
[ -z "$_processed_file" ] || chmod 600 "$_processed_file" 2>/dev/null || true

# The provisional CC-04 record (T0 timeout_fallback) is written
# by the bounded program itself, right after it arms its deadline and before
# any heavy import -- one interpreter start instead of two (measured
# 2026-09-27, ~40ms per edit). If the 2.5s advisory budget expires, the
# record still says T0 timeout_fallback; the rc!=0 re-stamp below covers an
# interpreter that never reached our code. Environment variables keep
# untrusted hook fields out of Python source interpolation.

# --- The CC-04 record writer the inline programs share --------------------------
# STDLIB ONLY, on purpose. $_py is whatever _get_python_path resolved, and its
# last resort is a bare `python3` that cannot import trw_mcp or its dependencies
# (structlog, trw_memory). The record has to be written exactly then: it is how
# an operator learns the hint engine could not be imported (`exception_fallback`
# plus the error), so the writer must not need the package that just failed.
# It gives the same guarantee as trw_mcp._checkout_write.write_checkout_file for
# this one path shape: every component below the checkout root is opened
# O_NOFOLLOW (a symlinked .trw, context or cc03-hints is refused, nothing is
# written), the temp file is created O_EXCL at 0600, and os.replace swaps the
# leaf without following a symlink planted there. Hook-owned constant text, no
# untrusted value: prepending it to a program does not reopen the source
# interpolation the single-quoted programs below exist to prevent.
_TRW_CC04_WRITER_PY='
def _trw_write_record(root, path, text):
    import os
    rel = os.path.relpath(path, root)
    parts = rel.split(os.sep)
    if os.path.isabs(rel) or ".." in parts or not parts[-1]:
        raise ValueError("record path is outside the checkout")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    isdir = getattr(os, "O_DIRECTORY", 0)
    fd = os.open(root, os.O_RDONLY | isdir)
    try:
        for part in parts[:-1]:
            try:
                os.mkdir(part, dir_fd=fd)
            except FileExistsError:
                pass
            nxt = os.open(part, os.O_RDONLY | isdir | nofollow, dir_fd=fd)
            os.close(fd)
            fd = nxt
        # A symlinked leaf is refused, as write_checkout_file refuses it: the
        # record is not written and the link is left as it was found.
        try:
            if (os.lstat(parts[-1], dir_fd=fd).st_mode & 0o170000) == 0o120000:
                raise OSError("record path is a symlink")
        except FileNotFoundError:
            pass
        tmp = "." + parts[-1] + "." + str(os.getpid()) + ".tmp"
        try:
            os.unlink(tmp, dir_fd=fd)
        except FileNotFoundError:
            pass
        tfd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600, dir_fd=fd)
        try:
            with os.fdopen(tfd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(tmp, parts[-1], src_dir_fd=fd, dst_dir_fd=fd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=fd)
            except OSError:
                pass
            raise
    finally:
        os.close(fd)
'

# --- Portable 2.5s bound for the hint subprocess (FR30) -----------------------
# This call used to read `timeout 2.5 "$_py" -c ...`. `timeout` is GNU
# coreutils and macOS ships neither it nor `gtimeout`, so on every Mac the
# command failed with 127 BEFORE the interpreter started: the fallback below
# fired on every single edit, the hint program never ran, and the CC-04
# correlation record reported a `timeout_fallback` for a timeout that had not
# happened (verified 2026-09-17). The deadline now lives where it is portable:
#   1. INSIDE the program (SIGALRM -> os._exit(124), installed as its first
#      statement) — the truthful bound whenever $_py is really an interpreter;
#   2. this function as the OUTER backstop for what an alarm cannot cover, an
#      interpreter that hangs before it runs our code. It delegates to
#      `timeout`/`gtimeout` where the box has one and uses a POSIX watchdog
#      (background child + `sleep` + `kill`) where it does not.
# Usage: _trw_bounded_python <seconds> <VAR=value ...> "$_py" -c <program>
_TRW_TIMEOUT_BIN=""
for _bp_cand in timeout gtimeout; do
    if command -v "$_bp_cand" >/dev/null 2>&1; then
        _TRW_TIMEOUT_BIN=$(command -v "$_bp_cand")
        break
    fi
done

_trw_bounded_python() {
    _bp_secs="$1"
    shift
    _bp_rc=0
    # The bounded child writes to a FILE, never straight into the caller's
    # command substitution: killing it does not kill a grandchild it left
    # behind (a wrapper script's `sleep`, a forked worker), and any survivor
    # holding the substitution pipe open makes the caller wait for the very
    # runtime this bound exists to cut -- measured 10s for a 1s bound under
    # dash and bash 3.2 before the file was introduced.
    _bp_out=$(mktemp 2>/dev/null) || _bp_out=""
    if [ -z "$_bp_out" ]; then
        # No mktemp: run unbuffered. The program's own SIGALRM still bounds it.
        env "$@" || _bp_rc=$?
        return $_bp_rc
    fi
    if [ -n "$_TRW_TIMEOUT_BIN" ]; then
        "$_TRW_TIMEOUT_BIN" "$_bp_secs" env "$@" > "$_bp_out" || _bp_rc=$?
    else
        env "$@" > "$_bp_out" &
        _bp_pid=$!
        ( sleep "$_bp_secs"; kill -TERM "$_bp_pid"; sleep 0.2; kill -KILL "$_bp_pid" ) >/dev/null 2>&1 &
        _bp_watch=$!
        wait "$_bp_pid" || _bp_rc=$?
        kill -TERM "$_bp_watch" 2>/dev/null || true
    fi
    cat "$_bp_out" 2>/dev/null || true
    rm -f "$_bp_out" 2>/dev/null || true
    return $_bp_rc
}

# TRW_EMBEDDINGS_ENABLED=false is load-bearing, not a tuning preference.
# Measured on a warm dev box (7 runs each, seconds):
#   embedding cold start in a FRESH process   14.48  (torch 1.76 + sentence-
#                                                    transformers 5.95 + MiniLM
#                                                    load 6.56 + encode 0.22)
#   compute_before_edit_hint, embeddings ON   14.2 - 14.6
#   compute_before_edit_hint, embeddings OFF   0.68 - 1.04
#   the sidecar read this hook exists for      0.003
# Every PreToolUse call spawns a NEW interpreter, so the model load is paid in
# full every time and is never amortized. Against the 2.5s budget that made
# distill_status="timeout_fallback" the only reachable outcome — T1 and T2 were
# unreachable here, and the T2 work is 3ms. Raising the budget is not the fix:
# it is capped by the 3000ms registered hook timeout (NFR06), and covering a
# 14.5s model load would add ~15s of latency to EVERY edit. Lexical recall still
# returns learnings (measured 0.44s first query, 0.03s after), so T1 survives.
# Scope is this subprocess only — the long-lived MCP server keeps hybrid recall,
# where the model is warm and a query costs 0.23s.
#
# PYTHONDONTWRITEBYTECODE/PYTHONOPTIMIZE were dropped here (measured
# 2026-09-27, hook-latency profile): they forced a full from-source recompile
# of the ~20-module trw_mcp/trw_memory import chain on EVERY edit (~190-192ms,
# reproduced 3x each way — 106ms with normal bytecode caching vs 295-297ms
# without it), because no .opt-1.pyc ever persisted. DONTWRITEBYTECODE exists
# to keep a subprocess from littering a developer's working tree with
# __pycache__; that does not apply here — $_py resolves to an installed
# interpreter (a released wheel's site-packages, or a project venv), and a
# cached .pyc for source that does not change between installs is always
# valid. If a future $_py ever pointed at a read-only or shared interpreter
# where a stray .pyc write would be unwelcome, the fix is
# PYTHONPYCACHEPREFIX pointed at a per-user cache dir, not this flag.
#
# MEMORY_DAEMON_AUTOSTART=false (disable this override with
# TRW_CC03_DAEMON_AUTOSTART=true in the hook's own environment) is load-bearing
# for the T1 recall this hint's compute_before_edit_hint call makes, for the
# same reason git_hooks/trw-post-commit.sh sets it: an advisory PreToolUse
# call must never pay to spawn-and-wait-for a daemon. Measured 2026-09-27
# (hook-latency profile, cProfile on a cold call): 0.961s of literal
# time.sleep() across 18 calls in the daemon-readiness poll
# (trw_memory/daemon/client.py:_attach, reached via
# trw_mcp/state/_daemon_store.py:_require_matching_security) -- ~46% of a
# 2.081s compute_before_edit_hint call and ~40% of the ~2.4-2.6s cold
# end-to-end wall time. With autostart off here, a cold call (no daemon
# already running) fails the T1 recall at once instead of spawning one and
# polling for it to come up; compute_before_edit_hint already treats that as
# "no lessons" and the T2 sidecar hint still serves (see
# trw_mcp/state/_memory_recall.py's StoreUnavailableError handling). A daemon
# already running (the common case: Claude Code sessions call
# trw_session_start() first per AGENTS.md, which starts one) answers exactly
# as before -- this only removes the wait for one THIS call would have had to
# start from cold. The long-lived MCP server and every other daemon caller
# keep the default autostart-and-wait behavior; this env var is scoped to
# this one subprocess.
_hint_output=$(
    _trw_bounded_python "${TRW_CC03_BOUND_S:-2.5}" \
    PYTHONPATH="$_wt_pythonpath${_wt_pythonpath:+${PYTHONPATH:+:}}${PYTHONPATH:-}" \
    TRW_EMBEDDINGS_ENABLED=false \
    TRW_PROJECT_ROOT="$_repo" \
    TRW_REPO_ROOT="$_repo" \
    MEMORY_DAEMON_AUTOSTART="${TRW_CC03_DAEMON_AUTOSTART:-false}" \
    TRW_CC04_HINTS_DIR="$_hints_dir" \
    TRW_CC04_TOOL_USE_ID="$_tool_use_id" \
    TRW_CC04_FILE_PATH="$_file_path" \
    TRW_CC03_HINT_FILES="$_hint_files" \
    TRW_CC03_SESSION_ID="$_session_id" \
    TRW_CC03_PROCESSED_FILE="$_processed_file" \
    TRW_CC03_BATCH_BUDGET_S="${TRW_CC03_BATCH_BUDGET_S:-1.6}" \
    TRW_CC03_REMEDY_RECEIPT="$_remedy_receipt" \
    TRW_CC03_PATH_BASE="$_path_base" \
    TRW_CC03_PROBE_DISTILL="$_cc03_probe" \
    "$_py" -c "$_TRW_CC04_WRITER_PY"'
# SINGLE-quoted on purpose. This program used to be double-quoted, so ${_file_path}
# — a model-controlled PreToolUse field — was spliced into Python SOURCE. A payload
# with file_path = x.py"+__import__("os").system("...")+".py executed as the
# developer, under a hook that needs no tool approval, and the hook still printed
# the ordinary beacon and exited 0. Reproduced before the fix; see CHANGELOG.
#
# The env vars below were already being exported for this subprocess and simply
# were not used on this path. The single quoting is what makes the mistake
# unrepeatable: no $ can be expanded here, so a future edit cannot reintroduce the
# interpolation without visibly changing the quoting. Every Python string below
# therefore uses double quotes. Same invariant as the sibling hooks —
# hooks/cursor/trw-before-edit-hint.sh and git_hooks/trw-post-commit.sh.
import os, sys, time
_trw_started = time.monotonic()
# The 2.5s budget belongs to THIS interpreter, because `timeout` is a GNU binary
# macOS does not ship (see _trw_bounded_python in the calling hook). os._exit,
# never an exception: the handler below would otherwise record
# exception_fallback for an event that is a genuine timeout. 124 is what
# `timeout` returned, so the shell fallback path is unchanged. 2.4s rather than
# 2.5 so this truthful in-process bound wins the race against the outer
# backstop whenever the interpreter is alive to answer.
try:
    import signal
    def _trw_on_deadline(_signum, _frame):
        os._exit(124)
    signal.signal(signal.SIGALRM, _trw_on_deadline)
    # TRW_CC03_ALARM_S: test-only override of the 2.4s default (never set in
    # production) -- shrinks the window a test needs to land a delay inside,
    # without racing the real 2.4s deadline in wall-clock time.
    try:
        _trw_alarm_s = float(os.environ.get("TRW_CC03_ALARM_S", "2.4"))
    except ValueError:
        _trw_alarm_s = 2.4
    signal.setitimer(signal.ITIMER_REAL, _trw_alarm_s)
except Exception:
    # A box without SIGALRM keeps the outer shell backstop; it does not lose
    # the hint. Nothing is swallowed here: the bound below is still enforced.
    pass
# Auto-mode gate (no explicit cc03 config): on only when trw-distill is
# importable by THIS interpreter. find_spec never imports it. Off, or a probe
# that fails in any way, exits 3 before any side effect: the hook stays silent.
if os.environ.get("TRW_CC03_PROBE_DISTILL") == "1":
    try:
        import importlib.util
        _trw_on = importlib.util.find_spec("trw_distill") is not None
    except Exception:
        _trw_on = False
    if not _trw_on:
        sys.stdout.flush()
        os._exit(3)
# Provisional CC-04 record, stdlib only (_trw_write_record, prepended above) and
# before the heavy imports below: a
# budget expiry from here on still leaves a truthful T0 timeout_fallback.
# Overwritten by write_hint_file() on a completed run. Never fatal.
try:
    import datetime, json, pathlib, re
    _trw_tuid = os.environ.get("TRW_CC04_TOOL_USE_ID", "")
    _trw_hdir = pathlib.Path(os.environ["TRW_CC04_HINTS_DIR"])
    if _trw_tuid and re.fullmatch(r"[A-Za-z0-9_.-]+", _trw_tuid):
        _trw_write_record(os.environ["TRW_PROJECT_DIR"], str(_trw_hdir / (_trw_tuid + ".json")), json.dumps({
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "file_path": os.environ.get("TRW_CC04_FILE_PATH", ""),
            "tier": "T0",
            "hint_emitted": False,
            "tokens_emitted": 0,
            "distill_status": "timeout_fallback",
            "tool_use_id": _trw_tuid,
            "outcome_captured": False,
            "was_edited": None,
            "edit_survived": None,
            "test_outcome": "unknown",
            "hint_acknowledged": None,
            # Nothing measured yet (8.2 S3) -- null, not a fabricated 0.
            "duration_ms": None,
            "sidecar_commits_behind": None,
            "target_changed_since_sidecar": None,
        }))
except Exception:
    pass
try:
    from trw_mcp.tools._before_edit_hint_core import T2_STATUSES, compute_before_edit_hint
    from trw_mcp.channels.claude_code._hook_helpers import (
        format_t1_hint, format_t2_hint, sidecar_remedy_once, sidecar_remedy_marker
    )
    file_path = os.environ.get("TRW_CC04_FILE_PATH", "")
    tool_use_id = os.environ.get("TRW_CC04_TOOL_USE_ID", "")
    processed_file = os.environ.get("TRW_CC03_PROCESSED_FILE", "")
    try:
        _batch_budget_s = float(os.environ.get("TRW_CC03_BATCH_BUDGET_S", "1.6"))
    except ValueError:
        _batch_budget_s = 1.6
    # One path per line (PRD-CORE-336-FR04): a Codex patch may touch several
    # files. The first is always computed; later ones start only while the
    # program is under the batch budget, so the batch never trips the 2.4s
    # deadline above (which would otherwise kill the interpreter mid-loop and
    # lose whatever the loop had not yet printed).
    files = [f for f in os.environ.get("TRW_CC03_HINT_FILES", "").split("\n") if f] or [file_path]
    parts = []
    tier = "T0"
    first_status = None
    first_as_of = None
    remedy_shown = False
    repo = pathlib.Path(os.environ["TRW_PROJECT_DIR"]).resolve()
    _path_base = os.environ.get("TRW_CC03_PATH_BASE") or os.getcwd()
    for index, target in enumerate(files):
        if index and time.monotonic() - _trw_started > _batch_budget_s:
            break
        # Recorded as ATTEMPTED before compute runs (CORE-336-S3-KI): the
        # shell reads this file to decide which files earn a 180s debounce
        # marker, so a file the budget check above skips (never reaching this
        # line) is correctly left un-debounced and eligible again next edit.
        if processed_file:
            try:
                with open(processed_file, "a", encoding="utf-8") as _pf:
                    _pf.write(target + "\n")
            except Exception:
                pass
        result = compute_before_edit_hint(
            file_path=str(pathlib.Path(os.path.join(_path_base, target)).resolve().relative_to(repo)),
            repo_root=str(repo),
        )
        if first_status is None:
            first_status = result.distill_status
            first_as_of = result.distill_as_of
        remedy = sidecar_remedy_once(
            pathlib.Path(os.environ["TRW_PROJECT_DIR"]), os.environ.get("TRW_CC03_SESSION_ID", ""),
            result.distill_status, result.distill_action,
        )
        if remedy and not remedy_shown:
            remedy_shown = True
            if os.environ.get("TRW_CC03_REMEDY_RECEIPT"):
                pathlib.Path(os.environ["TRW_CC03_REMEDY_RECEIPT"]).write_text(sidecar_remedy_marker(repo, os.environ.get("TRW_CC03_SESSION_ID", "")).name)
            parts.append(remedy)
            sys.stdout.write(remedy + "\n")
            sys.stdout.flush()
        hint = result.distill_hint
        learnings = [{"summary": l.summary} for l in result.learnings]
        if hint and result.distill_status in T2_STATUSES:
            part = format_t2_hint(
                file_path=target,
                risk_score=hint.risk_score,
                hotspot_warnings=hint.hotspot_warnings,
                co_change_neighbors=hint.co_change_neighbors,
                inferred_tests=hint.inferred_tests,
                lessons=hint.lessons,
                lessons_status=hint.lessons_status,
                as_of=result.distill_as_of,
                recall_learnings=learnings,
            )
            tier = "T2"
        elif learnings:
            part = format_t1_hint(learnings)
            tier = "T1" if tier == "T0" else tier
        else:
            continue
        formatted = part if len(files) == 1 else "-- " + target + " --\n" + part
        parts.append(formatted)
        # Printed AND flushed as each file completes (CORE-336-S3-KI): a later
        # target raising during compute, or the 2.4s SIGALRM os._exit(124)
        # firing mid-loop, can only cost hints from files not yet reached --
        # never the ones already written to this pipe.
        sys.stdout.write(formatted + "\n\n")
        sys.stdout.flush()
    # Disarm the 2.4s deadline: every compute+print above is already flushed
    # to the pipe, so nothing from here on should ever be cut off by it. Left
    # armed, a stray tick landing in the residual seconds between this point
    # and the natural end of this program fires os._exit(124) AFTER
    # write_hint_file() below has recorded the tier that was just printed --
    # the shell then discards the already-correct captured output (non-zero
    # exit means T0 beacon substitution) while the on-disk record still says
    # T2/T1, so the record and what the model saw disagree (observed: a
    # canary record read tier=T2 hint_available while the model got the T0
    # beacon). Disarming here closes that window: the record write below is a
    # cheap, bounded JSON write, so it cannot itself run long enough to
    # reintroduce the race.
    try:
        signal.setitimer(signal.ITIMER_REAL, 0)
    except Exception:
        pass
    output = "\n\n".join(parts)
    result_status = first_status
    # FR29: write hint file with tool_use_id
    if tool_use_id:
        from trw_mcp.channels.claude_code._hook_helpers import write_hint_file
        from pathlib import Path
        # In-process wall time of the computation this record describes (8.2
        # S3): measured against the same monotonic clock the SIGALRM budget
        # above uses, so it is directly comparable to the 2.4s deadline.
        _trw_duration_ms = (time.monotonic() - _trw_started) * 1000
        write_hint_file(
            hints_dir=Path(os.environ["TRW_CC04_HINTS_DIR"]),
            tool_use_id=tool_use_id,
            file_path=file_path,
            tier=tier,
            hint_emitted=bool(parts),
            tokens_emitted=len(output.split()),
            distill_status=result_status,
            duration_ms=_trw_duration_ms,
            sidecar_commits_behind=first_as_of.commits_behind if first_as_of is not None else None,
            target_changed_since_sidecar=first_as_of.target_changed if first_as_of is not None else None,
        )
except Exception as _exc:
    # Disarm here too (same reasoning as the success path above): the handler
    # below writes its own record and this program is about to end either way,
    # so nothing past this point should still be racing the 2.4s deadline.
    try:
        signal.setitimer(signal.ITIMER_REAL, 0)
    except Exception:
        pass
    # The provisional record written above says 'timeout_fallback' because it
    # is written BEFORE this bounded subprocess starts. Reaching this handler
    # means the subprocess RAN and RAISED — a broken venv ImportError, a
    # version-skew AttributeError, a real bug — which has nothing to do with
    # the 2.5s budget. Leaving the provisional record in place mixes those
    # events into the timeout count, so an operator debugging a low hit rate
    # tunes the timeout for a defect that is not about timing.
    #
    # Deliberately stdlib-only and self-contained (_trw_write_record): the import
    # that just failed must not be a precondition for recording that it failed. Untrusted hook
    # fields arrive via the environment, never interpolated into source — which
    # is now true of the whole program, not only of this handler.
    # "error" keeps what raised (PRD-FIX-155): 1,815 records in the TRW repo
    # said only exception_fallback, so none of them could say why.
    try:
        import datetime, json, pathlib, re
        _tuid = os.environ.get("TRW_CC04_TOOL_USE_ID", "")
        if _tuid and re.fullmatch(r"[A-Za-z0-9_.-]+", _tuid):
            _dir = pathlib.Path(os.environ["TRW_CC04_HINTS_DIR"])
            _trw_write_record(os.environ["TRW_PROJECT_DIR"], str(_dir / (_tuid + ".json")), json.dumps({
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
                "file_path": os.environ.get("TRW_CC04_FILE_PATH", ""),
                "tier": "T0",
                "hint_emitted": False,
                "tokens_emitted": 0,
                "distill_status": "exception_fallback",
                "error": (type(_exc).__name__ + ": " + str(_exc))[:300],
                # Elapsed until the exception, on the same monotonic clock as the
                # success path (8.2 S3) -- never fabricated as 0 when unmeasured.
                "duration_ms": (time.monotonic() - _trw_started) * 1000,
                "sidecar_commits_behind": None,
                "target_changed_since_sidecar": None,
                "tool_use_id": _tuid,
                "outcome_captured": False,
                "was_edited": None,
                "edit_survived": None,
                "test_outcome": "unknown",
                "hint_acknowledged": None,
            }))
    except Exception:
        pass
' 2>/dev/null
) && _hint_rc=0 || _hint_rc=$?

# Auto mode, trw-distill not importable: the hook is off. Nothing was written
# (the program exits before its first side effect), so there is nothing to
# debounce or record -- stay silent, as the old standalone probe did.
if [ "$_cc03_probe" -eq 1 ] && [ "$_hint_rc" -eq "$_TRW_CC03_AUTO_OFF_RC" ]; then
    rm -f "$_processed_file" 2>/dev/null || true
    exit 0
fi

# --- Debounce files the subprocess actually attempted (CORE-336-S3-KI) -----
# Read regardless of $_hint_rc: processed_file is appended to as each file
# starts, so a file already attempted before a timeout/exception killed the
# rest of the batch still earns its 180s window; a file the in-process batch
# budget skipped (never reaching that append) does not, and stays eligible on
# the very next edit instead of being silently swallowed for 180s.
#
# A TOTALLY empty processed_file (Python never wrote even its first line) is a
# different case from a budget skip: the interpreter died or failed to import
# before it could report ANYTHING, so there is no per-file signal to trust at
# all. Falling back to marking every original hint candidate there keeps the
# pre-CORE-336-S3-KI best-effort debounce for that failure -- a broken
# interpreter must not turn into "never debounce", spawning a fresh
# subprocess on every single edit of the same broken file forever.
_proc_marked_any=0
if [ -s "$_processed_file" ]; then
    while IFS= read -r _proc_path; do
        [ -n "$_proc_path" ] || continue
        _proc_mark=$(_trw_debounce_path "$_proc_path")
        date +%s | _trw_safe_write "$_proc_mark" || true
        _proc_marked_any=1
    done < "$_processed_file"
fi
# Plain `rm -f`, not `_trw_safe_rm`: this is the hook's own mktemp file in
# $TMPDIR (never checkout state), and `_trw_safe_rm`'s ancestor walk would
# refuse it on macOS anyway -- /var (mktemp's default parent) is itself a
# symlink to /private/var. Same reasoning as `_bp_out` in
# `_trw_bounded_python` above.
rm -f "$_processed_file" 2>/dev/null || true
if [ "$_proc_marked_any" -eq 0 ]; then
    while IFS= read -r _cand_path; do
        [ -n "$_cand_path" ] || continue
        _cand_mark=$(_trw_debounce_path "$_cand_path")
        date +%s | _trw_safe_write "$_cand_mark" || true
    done <<EOF_HINT_FILES
$_hint_files
EOF_HINT_FILES
fi

if [ "$_hint_rc" -ne 0 ]; then
    # Timeout or error: say nothing to the model (FR30, FR31).
    #
    # rc!=0 here is not only the truthful in-process 2.4s SIGALRM (already
    # disarmed before write_hint_file() runs, above) -- it is ALSO what the
    # OUTER shell watchdog (_trw_bounded_python's own SIGTERM at
    # TRW_CC03_BOUND_S, independent of the interpreter's own alarm) produces
    # when the total subprocess -- including a slow write_hint_file() itself,
    # or a box with no SIGALRM support at all -- runs past ITS deadline. That
    # watchdog has no way to know write_hint_file() already completed and
    # recorded a real tier before it fires, so the same
    # record-says-T2-but-the-model-got-T0 mismatch this hook's disarm fix
    # closed for the inner alarm can still happen through this second path.
    # Re-stamping the record here, unconditionally, closes it the same way the
    # initial provisional write does: whatever the record said a moment ago,
    # it now says exactly what is about to be printed. A record that already
    # said T0/timeout_fallback (the common case: nothing ran long enough to
    # write anything else) is simply re-written to the same values.
    if [ -n "$_tool_use_id" ]; then
        _trw_bounded_python "$_TRW_TAIL_BOUND_S" \
        "TRW_CC04_HINTS_DIR=$_hints_dir" \
        "TRW_CC04_TOOL_USE_ID=$_tool_use_id" \
        "TRW_CC04_FILE_PATH=$_file_path" \
        "$_py" -c "$_TRW_CC04_WRITER_PY"'
import datetime, json, os, pathlib, re
tool_use_id = os.environ["TRW_CC04_TOOL_USE_ID"]
if re.fullmatch(r"[A-Za-z0-9_.-]+", tool_use_id):
    hints_dir = pathlib.Path(os.environ["TRW_CC04_HINTS_DIR"])
    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "file_path": os.environ["TRW_CC04_FILE_PATH"],
        "tier": "T0",
        "hint_emitted": False,
        "tokens_emitted": 0,
        "distill_status": "timeout_fallback",
        "tool_use_id": tool_use_id,
        "outcome_captured": False,
        "was_edited": None,
        "edit_survived": None,
        "test_outcome": "unknown",
        "hint_acknowledged": None,
        "duration_ms": None,
        "sidecar_commits_behind": None,
        "target_changed_since_sidecar": None,
    }
    _trw_write_record(os.environ["TRW_PROJECT_DIR"], str(hints_dir / f"{tool_use_id}.json"), json.dumps(record))
    # A deadline exit can land between the temp file and its rename: this branch
    # is where that is noticed, so the leftovers are removed here.
    import time
    for _entry in os.scandir(hints_dir):
        if _entry.name.startswith(".") and _entry.name.endswith(".tmp") and not _entry.is_symlink():
            if time.time() - _entry.stat(follow_symlinks=False).st_mtime > 5:
                os.unlink(_entry.path)
' >/dev/null 2>&1 || true
    fi
    exit 0
fi

# --- CC-01 background snapshot refresh (PRD-CORE-231 FR01) ---
# _write_distill_snapshot_bg() has been defined in lib-distill-hint.sh since
# PRD-DIST-2405 but had ZERO call sites — the CC-01 memory-snapshot refresh
# never fired. Trigger it only after a real T2 result: a T1/T0 fallback carries
# no new distill intelligence worth snapshotting. Backgrounded and fail-silent,
# so it cannot add latency to (or fail) the PreToolUse call.
case "$_hint_output" in
    *"[TRW Distill Hint — T2]"*)
        _write_distill_snapshot_bg "$_py" "$_repo" 2>/dev/null || true
        ;;
esac

# --- Session-scoped identical-hint dedup (PRD-CORE-301 cut 2) ---
# The 180s debounce above bounds frequency; this bounds REPEATED, unchanged
# content once the debounce window has lapsed. Never suppresses a hint that
# changed, or the first hint for a file.
if [ -n "$_hint_output" ] && _distill_hint_already_seen "$_repo" "$_session_id:$_file_path" "$_hint_output"; then
    _hint_output=""
fi

# --- Output cap enforcement (FR32: 9500 char soft limit) ---
if [ -n "$_hint_output" ]; then
    _len=$(printf '%s' "$_hint_output" | wc -c) || _len=0
    if [ "$_len" -gt 9500 ] 2>/dev/null; then
        _hint_output=$(printf '%s' "$_hint_output" | head -c 9400)
        _hint_output="${_hint_output}
... (truncated — run trw_code(mode=\"hint\") for full context)"
    fi
    # Claude Code adds PLAIN stdout of a PreToolUse hook to nothing the model
    # reads: only hookSpecificOutput.additionalContext reaches Claude's context
    # (hooks reference, code.claude.com/docs/en/hooks). Printing the hint as
    # text meant no Claude Code user ever saw one. Emit exactly one JSON
    # object, no permissionDecision (the normal permission flow is unchanged).
    # The text travels in the ENVIRONMENT and is encoded by json.dumps, never
    # spliced into shell or Python source, so quotes, newlines or a forged
    # "}{" in a hint cannot break or add to the object. -I -S: stdlib only.
    _trw_bounded_python "$_TRW_TAIL_BOUND_S" "TRW_CC03_HINT_TEXT=$_hint_output" "$_py" -I -S -c '
import json, os, sys
sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": os.environ["TRW_CC03_HINT_TEXT"]}}) + "\n")
' 2>/dev/null && {
        # The receipt holds the marker's file name, written by the hint program
        # only when it printed the remedy line. Marking it delivered is a
        # millisecond shell write through the symlink-refusing writer: no third
        # interpreter start after the hint is already on stdout (review 2,
        # finding 10). Stale markers are pruned in the background.
        if [ -n "$_remedy_receipt" ] && [ -s "$_remedy_receipt" ]; then
            _remedy_name=$(head -n 1 "$_remedy_receipt" 2>/dev/null) || _remedy_name=""
            case "$_remedy_name" in
                sidecar-*[!0-9a-f]*.seen | "") ;;
                sidecar-*.seen)
                    ( umask 077; printf 'delivered\n' | _trw_safe_write "$_hints_dir/$_remedy_name" ) 2>/dev/null || true
                    (
                        find "$_hints_dir" -name 'sidecar-*.seen' -type f -mtime +7 2>/dev/null | while IFS= read -r _stale_seen; do
                            _trw_safe_rm "$_stale_seen" || true
                        done
                    ) </dev/null >/dev/null 2>&1 &
                    ;;
            esac
        fi
    }
fi

exit 0
