"""Canonical paths + no-follow, root-confined path resolution (PRD-SEC-013).

Every intent-contract artifact lives at a HARDCODED canonical location except
the contract file and the telemetry file, which are typed-config knobs. The
override ledger and its checkpoint are deliberately NOT configurable (R13):
a config-layer path override is a parallel-ledger substitution surface.

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Literal

__all__ = [
    "APPROVALS_PATH",
    "BREAK_GLASS_DIR",
    "CHECKPOINT_PATH",
    "CONTRACTS_DIR",
    "ENROLLMENT_EVIDENCE_PATH",
    "ENROLLMENT_PATH",
    "LEDGER_PATH",
    "OPEN_VIOLATIONS_PATH",
    "PRE_COMMIT_CONFIG_PATH",
    "TRW_CONFIG_PATH",
    "ConfinementError",
    "TargetClass",
    "classify_target",
    "is_regular_file",
    "parse_root_arg",
    "read_bytes_nofollow",
    "repo_root",
    "resolve_under_root",
    "stray_options",
    "unlink_confined",
    "write_text_confined",
]

TargetClass = Literal["ok", "outside", "escaped"]

CONTRACTS_DIR = ".trw/contracts"
ENROLLMENT_PATH = ".trw/contracts/enrollment.yaml"

#: Durable, FILESYSTEM-ONLY evidence that this project has been enrolled at some
#: point. It exists so "never enrolled" can be established WITHOUT asking git.
#:
#: Everything else that proves enrollment (the marker, its HEAD/index record)
#: either lives in one deletable file or needs a working git, and "git present
#: but unable to answer" is an ORDINARY state with no attacker in it: a `.git`
#: file pointing at a pruned worktree, a partial clone, a syntax error in the
#: user's global gitconfig, dubious-ownership under a Docker/CI bind mount. All
#: four used to read as enrolled-unknown and blocked every write in projects
#: that never opted in.
#:
#: Deliberately OUTSIDE ``.trw/contracts/`` so that removing the contract
#: directory does not take the enrollment evidence with it.
ENROLLMENT_EVIDENCE_PATH = ".trw/intent-enrollment-evidence.yaml"
LEDGER_PATH = ".trw/contracts/intent-override-ledger.jsonl"
CHECKPOINT_PATH = ".trw/contracts/intent-override-ledger.checkpoint.json"
APPROVALS_PATH = ".trw/contracts/weaken-edit-approvals.jsonl"
BREAK_GLASS_DIR = ".trw/runtime/break-glass"
OPEN_VIOLATIONS_PATH = ".trw/runtime/intent-open-violations.json"
TRW_CONFIG_PATH = ".trw/config.yaml"
PRE_COMMIT_CONFIG_PATH = ".pre-commit-config.yaml"


def repo_root() -> Path:
    """Resolve the project root the control points operate against."""
    from trw_mcp.state._paths import resolve_project_root

    return resolve_project_root()


def parse_root_arg(args: list[str]) -> tuple[Path | None, list[str]]:
    """Pull an optional ``--root PATH`` out of *args* — returns ``(root, remaining_args)``.

    Shared by every module CLI here. Without it, ``--root`` was silently ignored
    and the command operated on whatever ``repo_root()`` resolved to (cwd-relative)
    rather than the directory the operator explicitly named — so `ledger verify`
    checked the wrong chain and `enrollment enroll` wrote the marker under the
    wrong project.
    """
    remaining: list[str] = []
    root: Path | None = None
    index = 0
    while index < len(args):
        if args[index] == "--root" and index + 1 < len(args):
            root = Path(args[index + 1])
            index += 2
            continue
        remaining.append(args[index])
        index += 1
    return root, remaining


def stray_options(args: list[str]) -> list[str]:
    """Leftover ``-``-prefixed arguments, after every known flag has been removed.

    Every CLI here MUST refuse on a non-empty result. A trailing ``--root`` with
    no value is the motivating case: :func:`parse_root_arg` deliberately leaves
    it in the argument list rather than swallowing it, but each ``main`` then
    dispatched on ``args[0]`` and ran against the DEFAULT root — silently doing
    the work under the very root the operator was trying to override, and exiting
    0. ``enrollment enroll --root`` minted a marker in the wrong project.
    """
    return [item for item in args if item.startswith("-")]


def classify_target(root: Path, raw: str) -> tuple[TargetClass, Path | None]:
    """Classify an agent-supplied path against *root*.

    * ``outside`` — not under *root* at all: no claim can anchor it (allow).
    * ``escaped`` — lexically under *root* but symlink-resolving out of it: the
      caller fails CLOSED if the lexical path is anchored by a claim.
    * ``ok`` — safe to evaluate. The returned path is the LEXICAL resolution
      (symlinks not collapsed), so reads still use ``O_NOFOLLOW``.
    """
    if not raw:
        return "outside", None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    lexical = Path(os.path.normpath(str(candidate)))
    try:
        lexical.relative_to(Path(os.path.normpath(str(root))))
    except ValueError:
        return "outside", None
    try:
        Path(os.path.realpath(str(lexical))).relative_to(Path(os.path.realpath(str(root))))
    except (ValueError, OSError):
        return "escaped", lexical
    return "ok", lexical


def resolve_under_root(root: Path, raw: str) -> Path | None:
    """Convenience wrapper: the resolved path, or ``None`` when it is not ``ok``."""
    classification, path = classify_target(root, raw)
    return path if classification == "ok" else None


def read_bytes_nofollow(path: Path) -> bytes:
    """Read *path*'s bytes, refusing to traverse a symlink at the final component.

    A symlink swapped in between an anchor match and this read raises ``OSError``
    (``ELOOP``) instead of silently retargeting to another file.
    """
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


class ConfinementError(OSError):
    """A write/unlink target resolved outside its root, or through a symlink."""


def _classify_absolute(root: Path, path: Path) -> tuple[TargetClass, Path | None]:
    """:func:`classify_target` with both sides pinned to absolute form first.

    ``classify_target`` re-anchors a RELATIVE raw path under *root*, which is
    right for an agent-supplied path and wrong for one this package built as
    ``root / <constant>``: a relative *root* would produce ``root/root/...``.
    Callers here always hold a real filesystem path, so both sides are made
    absolute against the cwd once, and the classification is unchanged for the
    already-absolute case.
    """
    return classify_target(Path(os.path.abspath(str(root))), os.path.abspath(str(path)))


def write_text_confined(root: Path, path: Path, text: str, *, exclusive: bool = False) -> None:
    """Create or replace *path*, refusing symlinks and anything outside *root*.

    This module has shipped :func:`read_bytes_nofollow` since the first revision
    precisely so a swapped symlink cannot retarget a READ — and then wrote every
    one of its own artifacts with a plain ``Path.write_text``. Replacing
    ``.trw/intent-enrollment-evidence.yaml`` with a symlink therefore made the
    security control create an attacker-named file ANYWHERE the user can write,
    reached from the real post-edit hook, from ``enrollment status``, and from
    ``refresh_hook_digest`` — which the INSTALLER calls (finding F-D,
    2026-07-25).

    Two guards, because they cover different halves:

    * :func:`classify_target` refuses a target that symlink-resolves out of
      *root*. That is what catches a swapped PARENT directory and a DANGLING
      final symlink (``O_NOFOLLOW`` sees neither: the first is not the final
      component, the second has no inode to trip on until ``O_EXCL`` is in play).
    * ``O_NOFOLLOW`` refuses a symlink at the final component even when it points
      back INSIDE the root, and ``O_EXCL`` (write-once callers) additionally
      refuses to create over anything that already exists, symlink included.

    Residual, stated rather than implied away: a parent directory swapped between
    :func:`classify_target` and ``os.open`` is a TOCTOU this does not close. It
    needs ``openat``-relative descent from a pinned root fd; the guards above
    reduce it to a race, not a one-command file drop.

    Raises :class:`ConfinementError` (an ``OSError``) for a rejected target and
    plain ``OSError`` for an ordinary I/O failure, so best-effort callers can
    keep a single ``except OSError``.
    """
    classification, resolved = _classify_absolute(root, path)
    if classification != "ok" or resolved is None:
        raise ConfinementError(
            errno.EPERM,
            f"refusing to write a target classified {classification!r} against root {root}",
            str(path),
        )
    resolved.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
    flags |= os.O_EXCL if exclusive else os.O_TRUNC
    descriptor = os.open(str(resolved), flags, 0o644)
    try:
        os.write(descriptor, text.encode("utf-8"))
    finally:
        os.close(descriptor)


def unlink_confined(root: Path, path: Path) -> bool:
    """Remove *path* when it is confined to *root*. True only if something went.

    ``os.unlink`` removes a SYMLINK itself and never its target, and it refuses a
    directory outright, so the confinement check plus unlink cannot be steered
    into deleting anything outside the project.
    """
    classification, resolved = _classify_absolute(root, path)
    if classification != "ok" or resolved is None:
        return False
    try:
        os.unlink(str(resolved))
    except OSError:
        return False
    return True


def is_regular_file(path: Path) -> bool:
    """The exact semantic of the hooks' ``[ -f "$path" ]``: follow, then require a file.

    The shell layer and the Python layer used to disagree about what counts as an
    artifact — ``[ -f ]`` versus ``Path.exists()`` — and a directory or a symlink
    to ``/dev/null`` sat in the gap: both edit-time hooks went INERT while
    ``enrollment status`` reported ``stale`` and exited 1, a tool asserting
    protection that was not running (finding F-F, 2026-07-25). Every Python-side
    presence test for an artifact the hooks also test goes through here so the
    two answers cannot drift apart again.
    """
    return path.is_file()
