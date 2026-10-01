"""Skill-directory removal for uninstall: prove each file is TRW's, remove it, prune empty dirs.

Belongs to the ``_uninstall_manifest.py`` facade, which re-exports these names. Split out to keep both
modules under the 350 effective-LOC gate.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from pathlib import Path, PurePosixPath

from ._safe_remove import path_refusal, remove_if_hash


def _lexists_strict(path: Path) -> bool:
    """False only for ``FileNotFoundError``; any other stat error counts as present (rule 4: never guess absent)."""
    try:
        os.lstat(path)
    except FileNotFoundError:  # trw-fail-silent-allow: ENOENT is the one proof of absence
        return False
    except OSError:  # trw-fail-silent-allow: cannot stat means cannot call it gone, so it is treated as present
        return True
    return True


_MAX_HASH_BYTES = 16 * 1024 * 1024
_TOO_LARGE = "too large"


def _hash_regular_file(path: Path, expected_size: int | None = None) -> tuple[str | None, str]:
    """``(sha256, "")`` of a regular file, or ``(None, reason)`` when it cannot be proven (never follows a symlink).

    Refuses a non-regular file (FIFO, device) without blocking, a size differing from *expected_size* or over
    the cap before reading, and hashes in chunks so memory stays bounded.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError:  # trw-fail-silent-allow: unreadable means unproven, so the caller keeps the file
        return None, "unreadable"
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None, "not a regular file"
        if info.st_size > _MAX_HASH_BYTES:
            return None, _TOO_LARGE
        if expected_size is not None and info.st_size != expected_size:
            return None, "edited (hash differs)"
        digest = hashlib.sha256()
        remaining = _MAX_HASH_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            digest.update(chunk)
        if remaining <= 0:
            return None, _TOO_LARGE
        return digest.hexdigest(), ""
    except OSError:  # trw-fail-silent-allow: unreadable means unproven, so the caller keeps the file
        return None, "unreadable"
    finally:
        os.close(fd)


def _not_owned_reason(path: Path, rel: Path, bundled_root: Path, recorded_hash: str) -> str | None:
    """``None`` when bytes read just now prove *path* is TRW's, else the per-cause reason it is kept."""
    if not bundled_root.is_dir():
        return "not in the bundled skill"
    shipped = bundled_root / rel
    if recorded_hash and rel == Path("SKILL.md"):
        digest, why = _hash_regular_file(path)
        return None if digest == recorded_hash else (why or "edited (hash differs)")
    shipped_size: int | None = None
    try:
        shipped_stat = os.lstat(shipped)
        if stat.S_ISREG(shipped_stat.st_mode):
            shipped_size = shipped_stat.st_size
    except FileNotFoundError:  # trw-fail-silent-allow: no shipped counterpart means no ownership proof
        pass
    except OSError:  # trw-fail-silent-allow: cannot stat the shipped file, so ownership is unproven and kept
        return "unreadable"
    if shipped_size is None:
        return "user-added"
    digest, why = _hash_regular_file(path, shipped_size)
    if digest is None:
        return why
    shipped_digest, _ = _hash_regular_file(shipped)
    if shipped_digest is None:
        return "unreadable"
    return None if digest == shipped_digest else "edited (hash differs)"


def _remove_proven(
    path: Path,
    rel: Path,
    target: Path,
    expected: str | None,
    captures: dict[str, list[str]],
    kept: list[tuple[Path, str]],
    failures: list[tuple[Path, str]],
) -> None:
    """Delete a file just proven TRW's, re-proving the bytes as they are captured (UNINSTALL-SKILL-DIR-CAPTURE).

    ``remove_if_hash`` renames the file into ``.trw/trash``, re-hashes THOSE bytes against *expected* and links
    them back on a mismatch, so an edit saved after the proof survives; nothing is unlinked. A capture joins
    *captures* for uninstall's one move to the system Trash.
    """
    if expected is None:
        kept.append((path, "unreadable"))
        return
    outcome = remove_if_hash(path, target, expected, key=rel.as_posix())
    if outcome.status == "removed":
        captures.setdefault("trashed", []).append(str(path))
        captures.setdefault("trashed_at", []).append(str(outcome.retained_at or ""))
    elif outcome.status == "kept" and outcome.published is None and outcome.retained_at is not None:
        # The folder moved mid-removal (codex KI): the bytes are not at *path*, so say where the copy is.
        failures.append((path, f"kept ({outcome.reason}); a copy is in {outcome.retained_at}"))
    elif outcome.status == "kept" and outcome.reason.startswith("bytes differ"):
        kept.append((path, "changed during uninstall"))
    elif outcome.status != "absent":
        where = f"; a copy is in {outcome.retained_at}" if outcome.retained_at is not None else ""
        failures.append((path, f"kept ({outcome.reason}){where}"))


def _expected_digest(rel: Path, bundled_root: Path, recorded_hash: str) -> str | None:
    """The digest a proven file must still have when captured: the recorded one for SKILL.md, else the shipped."""
    if recorded_hash and rel == Path("SKILL.md"):
        return recorded_hash
    return _hash_regular_file(bundled_root / rel)[0]


#: Kept-file reasons a re-run could resolve once the user fixes the cause (a symlink, an unreadable or
#: non-regular file, a file changed mid-run); every other reason (edited, user-added) is final and never
#: changes on its own, so it is reported like any preserved edit and must not hold ``.trw`` back.
_RERUN_CAN_CHANGE = frozenset({"symlink", "unreadable", "not a regular file", "changed during uninstall"})


def _remove_skill_dir(
    skill_dir: Path,
    target: Path,
    recorded_hash: str = "",
    own_keys: dict[Path, str] | None = None,
    captures: dict[str, list[str]] | None = None,
) -> tuple[list[tuple[Path, str]], list[tuple[Path, str]], bool]:
    """Delete only the files of a TRW skill that are provably TRW's; return ``(kept, failures, trw_left)``.

    ``trw_left`` is True when a kept file may still be TRW's (edited, unreadable, symlink, skill missing from the
    bundle), so the manifest record must stay; it is False when every kept path is provably user-added.

    Ownership is proven per file by relative path and content hash: ``SKILL.md`` against the manifest's
    recorded hash (when given), every other file against the bundled skill. Anything unproven (edited,
    user-added, symlink, unreadable, non-regular, skill missing from the bundle) is *kept* with its cause;
    a ``safe_remove`` refusal or a non-ENOTEMPTY ``rmdir`` error is a *failure*. Directories go bottom-up via
    ``os.rmdir`` only when they exist in the bundled tree; the walk is iterative; an unmodified ``SKILL.md``
    goes last even when user files remain, because a leftover one is a live Claude Code skill whose TRW tools
    are gone.
    """
    from ._utils import _DATA_DIR

    bundled_root = _DATA_DIR / "skills" / skill_dir.name
    captures = {} if captures is None else captures
    kept: list[tuple[Path, str]] = []
    failures: list[tuple[Path, str]] = []
    dirs: list[Path] = []
    stack = [skill_dir]
    if not _lexists_strict(skill_dir):
        return kept, failures, False  # an earlier run already removed it; only the record is left to drop
    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:  # trw-fail-silent-allow: an unlistable directory is kept whole and reported
            kept.append((directory, "unreadable"))
            continue
        for entry in entries:
            child = directory / entry.name
            rel = child.relative_to(skill_dir)
            if rel == Path("SKILL.md"):
                continue  # judged once, last, below (whatever its type)
            try:
                mode = os.lstat(child).st_mode
            except OSError:  # trw-fail-silent-allow: cannot stat means cannot prove, so keep
                kept.append((child, "unreadable"))
                continue
            if stat.S_ISLNK(mode):
                kept.append((child, "symlink"))
            elif stat.S_ISDIR(mode):
                if path_refusal(child, target):
                    kept.append((child, "symlink"))
                else:
                    dirs.append(child)
                    stack.append(child)
            elif not stat.S_ISREG(mode):
                kept.append((child, "not a regular file"))
            elif child in (own_keys or {}):
                kept.append((child, f"recorded file, disposition {own_keys[child]}"))  # type: ignore[index]
            elif (why := _not_owned_reason(child, rel, bundled_root, recorded_hash)) is not None:
                kept.append((child, why))
            else:
                _remove_proven(
                    child, rel, target, _expected_digest(rel, bundled_root, recorded_hash), captures, kept, failures
                )
    for directory in reversed(dirs):  # reverse pre-order: children before parents
        if not (bundled_root / directory.relative_to(skill_dir)).is_dir():
            kept.append((directory, "not in the bundled skill"))
        else:
            _rmdir_if_empty(directory, target, failures)
    manifest_file = skill_dir / "SKILL.md"
    if _lexists_strict(manifest_file):  # re-hashed here, at the act; deleted even when user files remain
        rel = Path("SKILL.md")
        if (why := _not_owned_reason(manifest_file, rel, bundled_root, recorded_hash)) is not None:
            kept.append((manifest_file, why))
        else:
            expected = _expected_digest(rel, bundled_root, recorded_hash)
            _remove_proven(manifest_file, rel, target, expected, captures, kept, failures)
    if not (kept or failures):
        _rmdir_if_empty(skill_dir, target, failures)
    bundled = bundled_root.is_dir()
    trw_left = any(
        path == manifest_file or not (why == "user-added" or (why == "not in the bundled skill" and bundled))
        for path, why in kept
    )
    return kept, failures, trw_left


def _rmdir_if_empty(directory: Path, target: Path, failures: list[tuple[Path, str]]) -> None:
    """``os.rmdir`` *directory*; non-empty is normal (kept files), any other refusal or error is a failure."""
    refusal = path_refusal(directory, target)
    if refusal:
        failures.append((directory, refusal))
        return
    try:
        os.rmdir(directory)
    except OSError as exc:
        if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST, errno.ENOENT):
            failures.append((directory, f"error removing {directory}: {exc}"))


def prune_empty_dirs(surface_root: Path, up_to: Path | None = None) -> None:
    """Remove now-empty directories under *surface_root*, then the dir itself if empty.

    With *up_to* (the project root), also each ancestor that is left empty, stopping below *up_to*:
    ``.agents/skills`` emptied by uninstall otherwise left an empty ``.agents/`` (E2E-UNINSTALL-EMPTY-DIRS).
    Only ``rmdir`` is used, so a directory with anything in it stays.
    """
    if not surface_root.is_dir() or surface_root.is_symlink():
        return
    subdirs = sorted(
        (p for p in surface_root.rglob("*") if p.is_dir() and not p.is_symlink()),
        key=lambda p: len(p.parts),
        reverse=True,
    )
    for d in subdirs:
        try:
            if not any(d.iterdir()):
                d.rmdir()
        except OSError:  # trw-fail-silent-allow: a dir that won't empty-check cleanly is left as-is
            continue
    current = surface_root
    while True:
        try:
            if any(current.iterdir()):
                return
            current.rmdir()
        except OSError:  # trw-fail-silent-allow: leaves the (non-empty or permission-denied) dir in place
            return
        parent = current.parent
        if up_to is None or parent == up_to or not parent.is_relative_to(up_to) or parent.is_symlink():
            return
        current = parent


def prune_scaffold_dirs(target: Path, only_client: str | None = None) -> None:
    """After an uninstall, remove the directories TRW's scaffold or client surfaces made, if now empty.

    INC-080: ``docs/`` (init's ``_TRW_DIRS``) and ``.codex/`` (a client surface root) outlived a full uninstall
    as empty directories. The candidates come from init's own scaffold list and the uninstall surface catalog --
    each surface's parent directories -- never a hand-typed list. Deepest first, ``rmdir`` only and never a
    symlink, so a directory holding anything at all stays. ``.trw`` is left to its own removal step.
    A scoped ``--ide`` removal passes *only_client*: just that client's surface directories are candidates, so
    the shared scaffold (``docs/``, other clients' roots) is never touched (INC-117).
    """
    from trw_mcp.bootstrap import _CLAUDE_SCAFFOLD_DIRS, _TRW_DIRS
    from trw_mcp.client_profiles.catalog import client_surfaces, uninstall_surfaces

    candidates: set[PurePosixPath] = set()
    scaffold = () if only_client else (*_TRW_DIRS, *_CLAUDE_SCAFFOLD_DIRS)
    surfaces = client_surfaces(only_client) if only_client else uninstall_surfaces()
    for rel in (*scaffold, *(surface.relpath for surface in surfaces if not surface.home_scoped)):
        rel_path = PurePosixPath(rel)
        if rel_path.parts and rel_path.parts[0] != ".trw":
            candidates.update(p for p in (rel_path, *rel_path.parents) if p.parts)
    for candidate in sorted(candidates, key=lambda p: len(p.parts), reverse=True):
        directory = target.joinpath(*candidate.parts)
        try:
            if directory.is_dir() and not directory.is_symlink() and not any(directory.iterdir()):
                directory.rmdir()
        except OSError:  # trw-fail-silent-allow: a directory that will not empty-check or rmdir cleanly stays
            continue
