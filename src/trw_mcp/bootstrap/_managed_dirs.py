"""Which directories update-project writes into, and which nested trees it must leave alone.

Belongs to the ``bootstrap`` package; consumed by ``_update_transaction`` (snapshot, rollback and
symlink guard) and ``_version_manifest`` (dirty-file preservation). Everything here answers one
question about a project-relative directory: is it a dir a writer writes into (managed), is it a
nested runtime tree or repository that is pruned (never scanned, snapshotted, written or deleted
into), or the user's own symlinked skill that is left untouched.

The managed set is derived once from the registries update-project writes from (the transaction
roots and the client-profile surfaces), never hand-copied, and every match is anchored to the
project root and casefolded.
"""

from __future__ import annotations

import errno
import functools
import os
import stat
from dataclasses import dataclass
from pathlib import Path

# G2 (installer refinement 5.1.0): the change-counter's file discovery is this
# allow-listed directory scan, and it silently missed `.grok` when grok became
# a client — every file `update-project --ide grok` wrote landed outside the
# diff, so a run that provisioned 12 new files reported "0 created". Audited
# against every root-level UninstallSurface directory in
# `client_profiles.catalog` (test_update_transaction_dirs_cover_every_client
# parametrizes over `builtin_client_ids()` so a future client addition fails
# loudly here instead of repeating this silently).
_TRANSACTION_DIRS: tuple[str, ...] = (
    ".agents",
    ".antigravitycli",
    ".claude",
    ".codex",
    ".cursor",
    ".github",
    ".grok",
    ".opencode",
    ".vscode",
    # PRD-FIX-118/R8 hook-env-per-client: one file per client, never a single
    # shared hook-env.sh (see bootstrap/_file_ops.py::_write_hook_env_file).
    ".trw/runtime/hook-env.d",
)


# Nested runtime/tooling directories that update-project does NOT manage and that
# legitimately contain symlinks: Claude Code agent worktrees (``.claude/worktrees/``),
# any nested git worktree/repo/submodule, and package-manager/build dirs a client
# config tree may nest (e.g. ``.opencode/node_modules/.bin/*`` npm shims, virtualenvs,
# caches). The symlink-escape guard only needs to cover TRW-managed content — scanning
# these subtrees made a stray symlink abort the whole update (reports 2026-07-18:
# ``.claude/worktrees/.../evals/LATEST`` and ``.opencode/node_modules/.bin/node-which``,
# which left FRAMEWORK.md stuck at v25). Pruning is safe: update-project writes nothing
# into these subtrees, so no symlink there can redirect a managed write.
_PRUNED_NESTED_DIR_NAMES: frozenset[str] = frozenset(
    {"worktrees", "node_modules", ".venv", "venv", ".git", "__pycache__", ".next", ".turbo"}
)


@dataclass(frozen=True)
class _ManagedTables:
    """The managed surface as casefolded, project-relative part tuples (see :func:`_managed_tables`)."""

    exact: frozenset[tuple[str, ...]]  # dirs update writes into directly; a marker here does not make a repo of them
    owned: frozenset[tuple[str, ...]]  # TRW-owned dirs; the whole subtree is TRW's
    containers: frozenset[tuple[str, ...]]  # ``skills`` dirs: only canonical-named children are TRW's
    skill_roots: frozenset[tuple[str, ...]]  # every client's ``<client dir>/skills``, TRW writes there or not
    canonical_skills: frozenset[str]
    skill_subdirs: frozenset[tuple[str, ...]]  # dirs (relative to a container) the bundled skill corpus fills


def _fold(parts: tuple[str, ...]) -> tuple[str, ...]:
    """Casefold for comparison: the default macOS and Windows filesystems ignore case."""
    return tuple(part.casefold() for part in parts)


@functools.lru_cache(maxsize=1)
def _managed_tables() -> _ManagedTables:
    """Derive the managed surface once from the registries update-project already writes from.

    Every relpath ``uninstall_surfaces()`` registers (client dirs and files, all profiles) plus the
    transaction roots. A registered path is TRW-owned wholesale, except a ``skills`` dir whose
    children are TRW's only when canonical-named. Every ancestor dir of a registered path (for example
    ``.github/hooks`` above ``hooks.json``) is managed too, because a writer writes into it, but only
    that dir, not a subtree the user may own.
    """
    from trw_mcp.client_profiles.catalog import uninstall_surfaces

    from ._client_skills import canonical_skills_dir

    registered = {Path(rel).parts for rel in _TRANSACTION_DIRS}
    registered |= {Path(surface.relpath).parts for surface in uninstall_surfaces() if not surface.home_scoped}
    exact = {parts[:k] for parts in registered for k in range(1, len(parts))} | {
        Path(rel).parts for rel in _TRANSACTION_DIRS
    }
    containers = {parts for parts in registered if parts[-1] == "skills"}
    # A third-party skills installer links its skills into EVERY client's skills dir, whether or not TRW installs
    # skills there (Grok's `.grok/skills`): a client dir is any dot-dir some registered surface lives in. Derived,
    # so a new client is covered without an edit here. Only the link exemption reads this, never a write.
    skill_roots = containers | {
        (parts[0], "skills")
        for parts in registered
        if len(parts) >= 2 and parts[0].startswith(".") and parts[0] != ".trw"
    }
    skills_root = canonical_skills_dir()
    canonical: set[str] = set()
    subdirs: set[tuple[str, ...]] = set()
    if skills_root.is_dir():
        for skill in (d for d in skills_root.iterdir() if d.is_dir()):
            canonical.add(skill.name)
            subdirs.add((skill.name,))
            subdirs |= {(skill.name, *Path(d).relative_to(skill).parts) for d, _dirs, files in os.walk(skill) if files}
    owned = registered - containers - {Path(rel).parts for rel in _TRANSACTION_DIRS}
    return _ManagedTables(
        exact=frozenset(map(_fold, exact | containers)),
        owned=frozenset(map(_fold, owned)),
        containers=frozenset(map(_fold, containers)),
        skill_roots=frozenset(map(_fold, skill_roots)),
        canonical_skills=frozenset(name.casefold() for name in canonical),
        skill_subdirs=frozenset(map(_fold, subdirs)),
    )


def _managed_kind(path: Path, root: Path) -> str | None:
    """``"owned"`` / ``"exact"`` when *path* (under project *root*) is a dir update writes into, else ``None``.

    Anchored to *root*, so a same-named dir elsewhere is never managed. The derivation is
    :func:`_managed_tables`; nothing here hand-copies the surface list.
    """
    try:
        parts = _fold(path.relative_to(root).parts)
    except (
        ValueError
    ):  # trw-fail-silent-allow: relative_to ValueError is the answer "outside the project root", not managed
        return None
    tables = _managed_tables()
    for k in range(1, len(parts) + 1):
        prefix = parts[:k]
        if prefix in tables.owned or (
            k >= 2 and prefix[:-1] in tables.containers and prefix[-1] in tables.canonical_skills
        ):
            return "owned"
    return "exact" if parts in tables.exact else None


def _is_written_dir(path: Path, root: Path) -> bool:
    """True when a writer writes files directly into *path*: a registered owned dir or a dir of the
    bundled skill corpus under a ``skills`` container. Deeper dirs of an owned subtree are not written."""
    try:
        parts = _fold(path.relative_to(root).parts)
    except ValueError:  # trw-fail-silent-allow: relative_to ValueError is the answer "outside the project root"
        return False
    tables = _managed_tables()
    if parts in tables.owned:
        return True
    return any(parts[: len(c)] == c and parts[len(c) :] in tables.skill_subdirs for c in tables.containers)


_GITFILE_READ_LIMIT = 4096


def _is_submodule_checkout(path: Path, root: Path) -> bool:
    """True only for a clear submodule of THIS project: ``path/.git`` is a regular file (never a link or
    dir) whose ``gitdir: <p>`` resolves strictly under the project's own ``.git/modules/`` (the realpath
    of ``root/.git``, itself a real directory). At most 4 KiB is read. Anything else (a forged
    ``../../x/.git/modules/y``, a worktree pointer, a project whose ``.git`` is a file) is not "clearly
    a submodule", so callers fail closed: a pruned dir skips both validation and the snapshot."""
    try:
        if not stat.S_ISDIR(os.lstat(root / ".git").st_mode):
            return False
        modules = Path(os.path.realpath(root / ".git")) / "modules"
        if not stat.S_ISREG(os.lstat(path / ".git").st_mode):  # a FIFO/device would block the open
            return False
        fd = os.open(path / ".git", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return False
            head = os.read(fd, _GITFILE_READ_LIMIT).decode("utf-8", errors="strict")
        finally:
            os.close(fd)
    except (OSError, UnicodeDecodeError):  # trw-fail-silent-allow: unreadable is "not clearly a submodule": fail closed
        return False
    if not head.startswith("gitdir:"):
        return False
    try:
        gitdir = Path(os.path.realpath(path / head[len("gitdir:") :].strip()))
    except ValueError:  # trw-fail-silent-allow: an embedded NUL is "not clearly a submodule": fail closed
        return False
    return gitdir != modules and gitdir.is_relative_to(modules)


def _is_user_skill_link(path: Path, root: Path) -> bool:
    """A symlinked child of a ``skills`` dir whose name is not a canonical TRW skill: the user's own
    (typically a checkout elsewhere). Update never writes it, so it is left untouched, never followed."""
    if not path.is_symlink():
        return False
    try:
        parts = _fold(path.relative_to(root).parts)
    except (
        ValueError
    ):  # trw-fail-silent-allow: relative_to ValueError is the answer "outside the project root", not managed
        return False
    tables = _managed_tables()
    return len(parts) >= 2 and parts[:-1] in tables.skill_roots and parts[-1] not in tables.canonical_skills


def _has_git_marker(path: Path, *, denied_is_marker: bool = False) -> bool:
    """True when the real directory *path* holds a ``.git`` (file, dir or link).

    Never follows a symlink: *path* is opened ``O_NOFOLLOW`` and ``.git`` is looked up relative to that
    descriptor, so a swap to a symlink between the check and the probe cannot redirect it. Only
    ``FileNotFoundError`` means absent (a link, a non-dir or a missing path is "no marker"); an
    ``EACCES`` surfaces instead of reading as "no repo", except with *denied_is_marker* (rollback):
    a rollback must not raise part-way through, so an unreadable dir counts as a marker (preserved).
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except (FileNotFoundError, NotADirectoryError):  # trw-fail-silent-allow: absent or not a dir is "no marker"
        return False
    except OSError as exc:  # trw-fail-silent-allow: ELOOP (a symlink) is never probed; any other errno is re-raised
        if exc.errno == errno.ELOOP:  # a symlink: never probed
            return False
        if denied_is_marker and exc.errno == errno.EACCES:
            return True
        raise
    try:
        os.lstat(".git", dir_fd=fd)
    except FileNotFoundError:  # trw-fail-silent-allow: only ENOENT means no .git
        return False
    except PermissionError:
        if denied_is_marker:
            return True
        raise
    finally:
        os.close(fd)
    return True


def _is_pruned_nested_dir(path: Path, root: Path, *, denied_is_marker: bool = False) -> bool:
    """A nested directory update-project must neither scan nor snapshot: a Claude
    Code ``worktrees`` container, a package-manager/build dir, or any nested git
    worktree/repo/submodule (a ``.git`` file, dir or link marks one).

    A dir update itself writes into (:func:`_managed_kind`) is never pruned by a marker: a planted
    ``.git`` must not remove it from the snapshot or the symlink guard (the snapshot refuses instead,
    see :func:`_refuse_git_marked_owned_dirs`). The one exception: a clear submodule checkout
    (:func:`_is_submodule_checkout`) DEEPER in an owned subtree, which no writer writes into, is pruned;
    any other marker there fails closed (refused). A symlinked *path* is never probed."""
    if path.name in _PRUNED_NESTED_DIR_NAMES:
        return True
    kind = _managed_kind(path, root)
    if kind == "owned" and not _is_written_dir(path, root):
        return _has_git_marker(path, denied_is_marker=denied_is_marker) and _is_submodule_checkout(path, root)
    if kind is not None:
        return False
    return _has_git_marker(path, denied_is_marker=denied_is_marker)


def _is_under_pruned_dir(target_dir: Path, rel: str) -> bool:
    """True when ``rel`` lies at or below a pruned nested dir (same rule as the
    snapshot ignore). The ``.git`` probe skips symlinked ancestors, so a symlink
    cannot make a managed path look pruned; a NAME match (e.g. a symlink named
    ``worktrees``) still prunes, which is benign because a skip means "leave
    untouched"."""
    current = target_dir
    for part in Path(rel).parts:
        current /= part
        if part in _PRUNED_NESTED_DIR_NAMES:
            return True
        if not current.is_symlink() and _is_pruned_nested_dir(current, target_dir):
            return True
    return False
