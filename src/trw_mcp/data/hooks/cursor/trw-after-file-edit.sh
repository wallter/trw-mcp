#!/usr/bin/env bash
# TRW Cursor hook — afterFileEdit
# Observer: logs file modification and records it as change evidence. No output injection.
#
# Change evidence (INC-115): the deliver gate decides "did this session change code?" from
# `file_modified` records in .trw/context/session-events.jsonl, in the same flat shape that
# data/hooks/post-tool-event.sh writes for claude-code. Without this write the count read 0
# for a Cursor session and trw_deliver succeeded with no build.
set -euo pipefail

_LOG_DIR="${CURSOR_PROJECT_DIR:-${PWD}}/.trw/logs"
_LOG_FILE="${_LOG_DIR}/cursor-hooks.jsonl"
# PRD-SEC/RC8: refuse to log through a symlinked .trw, .trw/logs, or log
# file -- a crafted checkout must not be able to redirect this append at an
# arbitrary target the user can write. Logging is best-effort (never blocks
# the gate this hook may also be deciding), so the fix is to silently drop
# the log line, not to abort.
if [ -L "${_LOG_DIR%/logs}" ] || [ -L "$_LOG_DIR" ] || [ -L "$_LOG_FILE" ]; then
    _LOG_FILE="/dev/null"
fi

_log() {
  mkdir -p "${_LOG_DIR}" 2>/dev/null || true
  printf '{"ts":"%s","level":"info","component":"cursor-hook","event":"afterFileEdit","msg":%s}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$(printf '%s' "$1" | python3 -I -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null || echo '"<log-error>"')" \
    >> "${_LOG_FILE}" 2>/dev/null || true
}

_INPUT="$(cat)"
_FILE="$(printf '%s' "${_INPUT}" | python3 -I -c 'import json,sys; d=json.load(sys.stdin); print(d.get("file_path","unknown"))' 2>/dev/null || echo "unknown")"
_log "afterFileEdit file=${_FILE}"

# One flat record per edit with a repo-relative path. Every component below the repo root is opened relative to a
# pinned directory descriptor with no-follow, so a path swapped for a symlink after a check cannot redirect the
# write. Between 8 and 9 MiB only a change_evidence_unknown record is appended (the gate then fails closed instead
# of reading zero); past 9 MiB nothing is. Fail-open: never a non-zero exit.
printf '%s' "${_INPUT}" | TRW_ROOT="${CURSOR_PROJECT_DIR:-${PWD}}" python3 -I -c '
import json, os, sys, time
def open_dir_below(parent, name):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        return os.open(name, flags, dir_fd=parent)
    except FileNotFoundError:
        os.mkdir(name, 0o755, dir_fd=parent)
        return os.open(name, flags, dir_fd=parent)
fds = []
try:
    d = json.load(sys.stdin)
    f = str(d.get("file_path") or "")
    if not f or len(f) > 4096:
        raise SystemExit(0)
    root = os.path.realpath(os.environ["TRW_ROOT"])
    rel = os.path.relpath(os.path.join(os.path.realpath(os.path.dirname(f)), os.path.basename(f)), root) if os.path.isabs(f) else f
    sid = (os.environ.get("TRW_SESSION_ID") or str(d.get("conversation_id") or d.get("session_id") or ""))[:200]
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rec = {"ts": ts, "event": "file_modified", "tool": "cursor:afterFileEdit", "file": rel, "session_id": sid, "pinned": False}
    fds.append(os.open(root, os.O_RDONLY | os.O_DIRECTORY))
    fds.append(open_dir_below(fds[-1], ".trw"))
    fds.append(open_dir_below(fds[-1], "context"))
    fds.append(os.open("session-events.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644, dir_fd=fds[-1]))
    size = os.fstat(fds[-1]).st_size
    if size >= 9 * 1024 * 1024:
        raise SystemExit(0)
    if size >= 8 * 1024 * 1024:
        rec = {"ts": ts, "event": "change_evidence_unknown", "reason": "stream_full"}
    os.write(fds[-1], (json.dumps(rec) + "\n").encode("utf-8"))
except Exception:
    pass
finally:
    for fd in reversed(fds):
        os.close(fd)
' >/dev/null 2>&1 || true

printf '{}\n'
