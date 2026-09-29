"""Reusable filesystem-hazard fixtures for code that deletes, overwrites, retires or restores user files.

Each helper maps to a line of the checklist in ``tests/AGENTS.md`` ("Changes that delete, overwrite,
retire, restore or seed user files"). Plain helper module, imported explicitly::

    from tests._fs_hazards import race_after, swap_to_symlink

Known limits (documented, not fixed): the helpers assume POSIX (no Windows guards: symlink, chmod
and open-fd semantics differ), and the ``race_after``/``fail_nth_read`` injection matches the path
in the FIRST positional argument(s) only, so a call that passes the path as a keyword (``os.stat(path=p)``,
``open(file=p)``) bypasses the injection and the probe stays unfired.

Recipes for the two races that beat every capture/restore design round so far::

    # a racer replaces our just-created placeholder before we rename onto it
    race_after(monkeypatch, target=placeholder, op="rename", when="before",
               interloper=lambda: atomic_replace(placeholder, b"racer"))
    # a racer replaces a file between our lstat of it and our unlink of it
    race_after(monkeypatch, target=path, op="lstat", nth=<the act-time lstat>,
               interloper=lambda: atomic_replace(path, b"user edit"))

``interloper`` must be a callable (note the ``lambda``): the swap helpers act immediately, so
passing ``atomic_replace(...)`` itself would mutate before the race is armed. Pair either with
``assert_user_bytes_preserved``. Only ``os.link`` or a direct ``O_EXCL`` write
puts bytes under a visible name safely; a rename onto a name you created earlier does not.

Nothing here is a fixture plugin or autouse. Always assert ``probe.fired`` so a refactor that stops
reaching the raced call cannot turn the test vacuous.
"""

from __future__ import annotations

import builtins
import contextlib
import hashlib
import io
import os
import stat
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

_OS_OPS = frozenset({"stat", "lstat", "open", "unlink", "replace", "rename", "link", "mkdir"})
_PATH_OPS = frozenset({"read_bytes", "read_text", "exists", "is_file"})
RACE_OPS = _OS_OPS | _PATH_OPS


def _key(value: object) -> str | None:
    """Normalised string for a path-like argument, without touching the filesystem."""
    if isinstance(value, (str, bytes, os.PathLike)):
        out = os.fspath(value)
        return out.decode(errors="surrogateescape") if isinstance(out, bytes) else out
    return None


@dataclass
class Probe:
    """Observation handle for :func:`race_after`."""

    fired: bool = False
    matches: int = 0  # calls on the target seen so far (excluding the interloper's own)
    _busy: bool = field(default=False, repr=False)


def race_after(
    monkeypatch: pytest.MonkeyPatch,
    *,
    target: Path | str,
    op: str,
    interloper: Callable[[], object],
    nth: int = 1,
    when: str = "after",
) -> Probe:
    """Run ``interloper()`` once, around the ``nth`` call of ``op`` on ``target``.

    ``when="after"`` runs it once the real call has returned (the caller acts on a stale answer);
    ``"before"`` runs it first (the real call sees the swapped state). For ``link``/``replace``/``rename``
    either the source or the destination may match. Calls made by the interloper itself are not counted.
    """
    if op not in RACE_OPS:
        raise ValueError(f"unsupported op {op!r}; choose from {sorted(RACE_OPS)}")
    if when not in ("after", "before"):
        raise ValueError("when must be 'after' or 'before'")
    if nth < 1:
        raise ValueError("nth must be >= 1")
    want = _key(target)
    probe = Probe()

    def hit(args: tuple[object, ...]) -> bool:
        limit = 2 if op in ("link", "replace", "rename") else 1
        return want in {_key(a) for a in args[:limit]}

    def wrap(real: Callable[..., object]) -> Callable[..., object]:
        def wrapper(*args: object, **kwargs: object) -> object:
            if probe._busy or probe.fired or not hit(args):
                return real(*args, **kwargs)
            probe.matches += 1
            trigger = probe.matches == nth
            if trigger and when == "before":
                _fire(probe, interloper)
            result = real(*args, **kwargs)
            if trigger and when == "after":
                _fire(probe, interloper)
            return result

        return wrapper

    if op in _OS_OPS:
        monkeypatch.setattr(os, op, wrap(getattr(os, op)))
    else:
        monkeypatch.setattr(Path, op, wrap(getattr(Path, op)))
    return probe


def _fire(probe: Probe, interloper: Callable[[], object]) -> None:
    probe.fired = True
    probe._busy = True
    try:
        interloper()
    finally:
        probe._busy = False


# ---- act-time swap interlopers -------------------------------------------------------------------


def swap_to_symlink(path: Path, dest: Path | str) -> None:
    """Replace ``path`` (file or dir) by a symlink to ``dest``; the original bytes move aside as ``<name>.swapped``."""
    aside = path.with_name(path.name + ".swapped")
    os.replace(path, aside)
    os.symlink(dest, path)


def swap_to_dir(path: Path) -> None:
    """Replace the file ``path`` by an empty directory; the original bytes move aside as ``<name>.swapped``."""
    aside = path.with_name(path.name + ".swapped")
    os.replace(path, aside)
    os.mkdir(path)


def atomic_replace(path: Path, data: bytes) -> None:
    """Write ``data`` via tmp + ``os.replace`` (an editor save): new inode, same name."""
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.edit")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def edit_in_place(path: Path, data: bytes) -> None:
    """Overwrite ``path`` through the same inode (truncate + write)."""
    with open(path, "r+b") as fh:
        fh.truncate(0)
        fh.write(data)


class open_fd_writer:
    """Hold an fd on ``path`` opened NOW; ``write`` later goes through it.

    Models the edit that survives an unlink-by-name: the writer's bytes land in an inode that a
    name-based delete has already detached. Open it before the code under test runs its check.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = os.open(path, os.O_WRONLY)

    def write(self, data: bytes) -> None:
        assert self._fd is not None, "writer already closed"
        os.ftruncate(self._fd, 0)
        os.pwrite(self._fd, data, 0)

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> open_fd_writer:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ---- read failures -------------------------------------------------------------------------------


@dataclass
class Counter:
    """Reads of the watched path seen, and how many were failed."""

    calls: int = 0
    raised: int = 0
    _depth: int = field(default=0, repr=False)


def fail_nth_read(
    monkeypatch: pytest.MonkeyPatch,
    path: Path | str,
    *,
    nth: int,
    exc: type[BaseException] = PermissionError,
) -> Counter:
    """Raise ``exc`` on exactly the ``nth`` read of ``path`` (read_bytes/read_text/open for read/os.open read)."""
    want = _key(path)
    counter = Counter()

    def gate(target: object, reading: bool) -> None:
        if not reading or _key(target) != want:
            return
        counter.calls += 1
        if counter.calls == nth:
            counter.raised += 1
            raise exc(13, "injected read failure", want)

    def outer(real: Callable[..., object], target_of: Callable[..., object], reading_of: Callable[..., bool]):
        def wrapper(*args: object, **kwargs: object) -> object:
            if counter._depth == 0:
                gate(target_of(*args, **kwargs), reading_of(*args, **kwargs))
            counter._depth += 1
            try:
                return real(*args, **kwargs)
            finally:
                counter._depth -= 1

        return wrapper

    def open_reads(*args: object, **kwargs: object) -> bool:
        mode = args[1] if len(args) > 1 else kwargs.get("mode", "r")
        return isinstance(mode, str) and "r" in mode and "+" not in mode

    def os_open_reads(*args: object, **kwargs: object) -> bool:
        flags = args[1] if len(args) > 1 else kwargs.get("flags", 0)
        return isinstance(flags, int) and (flags & (os.O_WRONLY | os.O_RDWR)) == 0

    def first(*args: object, **kwargs: object) -> object:
        return args[0] if args else kwargs.get("file")

    for owner, name, target_of, reading_of in (
        (Path, "read_bytes", first, lambda *a, **k: True),
        (Path, "read_text", first, lambda *a, **k: True),
        (builtins, "open", first, open_reads),
        (io, "open", first, open_reads),
        (os, "open", first, os_open_reads),
    ):
        monkeypatch.setattr(owner, name, outer(getattr(owner, name), target_of, reading_of))
    return counter


@contextlib.contextmanager
def _chmod_blocked(target: Path, mode: int) -> Iterator[None]:
    if os.name != "posix":
        pytest.skip("POSIX mode bits only: Windows has no chmod 000, so unreadable hazards cannot be simulated")
    if os.geteuid() == 0:
        pytest.skip("root bypasses permission bits; unreadable hazards cannot be simulated")
    original = stat.S_IMODE(os.lstat(target).st_mode)
    os.chmod(target, mode)
    try:
        yield
    finally:
        os.chmod(target, original)


def unreadable(path: Path) -> contextlib.AbstractContextManager[None]:
    """``path`` gets mode 000 (real chmod, restored in ``finally``); skips as root and on non-POSIX hosts."""
    return _chmod_blocked(path, 0o000)


def unreadable_parent(path: Path) -> contextlib.AbstractContextManager[None]:
    """``path.parent`` gets mode 000 so ``path`` cannot be stat'ed or read; restored in ``finally``; skips as root."""
    return _chmod_blocked(path.parent, 0o000)


# ---- staging-name collisions ---------------------------------------------------------------------

_STAGING_FORMS = (
    ".x.tmp",
    "x~",
    "x.retired",
    ".x.seeding-123",
    "x.bak",
    "x.orig",
    "x.new",
    ".x.swp",
    "x.partial",
    "x.lock",
)


def name_collision_corpus(staging_patterns: list[str]) -> list[str]:
    """Adversarial user file names equal to staging/temp/backup forms, plus ``staging_patterns`` verbatim.

    A pattern containing ``{name}`` is also expanded with ``x``. Feed each name as a source and assert
    its bytes arrive byte-identical and are never consumed as the code's own scratch file.
    """
    out: list[str] = []
    for name in (*_STAGING_FORMS, *staging_patterns):
        for form in (name, name.format(name="x") if "{name}" in name else name):
            if form not in out:
                out.append(form)
    return out


# ---- whole-tree user-byte accounting -------------------------------------------------------------


def _digest_of(full: Path, root_real: Path) -> str | None:
    """sha256 of the bytes reachable at ``full``; ``None`` for a link leaving root, a dangling link, or an unreadable file."""
    target = full
    if full.is_symlink():
        target = full.resolve()
        inside = target.is_relative_to(root_real)
        if not inside or not target.is_file():
            return None
    try:
        return hashlib.sha256(target.read_bytes()).hexdigest()
    except OSError:  # trw-fail-silent-allow: unreadable is not reachable bytes
        return None


def snapshot_user_bytes(root: Path) -> dict[str, list[str]]:
    """Map sha256 of every readable file under ``root`` to the relative names that reach it.

    Hardlinks list each name; a symlink counts as a name for its target's bytes when the target is a
    regular file inside ``root``. Symlinked directories are not descended.
    """
    root_real = root.resolve()
    seen: dict[str, list[str]] = {}
    for dirpath, _dirs, files in os.walk(root, followlinks=False):
        for fname in files:
            full = Path(dirpath) / fname
            digest = _digest_of(full, root_real)
            if digest is not None:
                seen.setdefault(digest, []).append(str(full.relative_to(root)))
    return seen


def assert_user_bytes_preserved(before: dict[str, list[str]], root: Path) -> None:
    """Every sha256 in ``before`` must still be reachable under SOME name in ``root``."""
    after = snapshot_user_bytes(root)
    lost = {digest: names for digest, names in before.items() if digest not in after}
    assert not lost, "user bytes lost (sha256 -> former names): " + repr({d[:12]: n for d, n in lost.items()})
