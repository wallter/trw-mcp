"""The project a block of code works on, for code that cannot be handed it (B71-117, B71-118).

``init_project`` and ``update_project`` name their target here (``installing_into``), and so do
``audit`` and ``export`` when they read another checkout (``project_bound``).
``_paths.resolve_project_root`` answers with it before ``TRW_PROJECT_ROOT`` or the working
directory, so every zero-argument resolver inside the block (the managed-artifact manifest sources,
instruction sync, store counts, the run scan) resolves the target. A context variable, like
``agents._report_cap``, rather than ``TRW_PROJECT_ROOT``: it is local to the block's own context,
leaves ``os.environ`` and the cached config alone, and cannot leak into another thread or an
overlapping install. The flip side: a thread the block starts does not inherit it, so an install
submits that work through ``contextvars.copy_context().run``. Lives in ``state`` because ``_paths``
reads it and must not import the bootstrap package.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class _Install:
    root: Path
    #: What the install's own context caches for its target (``models.config`` keeps the target's
    #: config here rather than in the process-wide singleton); dropped when the install ends.
    cache: dict[str, Any] = field(default_factory=dict)
    #: Shared by the outermost install and every install nested in it (``state._daemon_store`` keeps
    #: the install's memory-daemon wait budget here, so a nested binding cannot restart it). ``None``
    #: for a ``project_bound`` block outside any install: its daemon waits are unbounded.
    shared: dict[str, Any] | None = None


_TARGET: ContextVar[_Install | None] = ContextVar("trw_install_target", default=None)


@contextmanager
def installing_into(target_dir: Path) -> Iterator[None]:
    """Name *target_dir* as the project being installed into, inside the block."""
    outer = _TARGET.get()
    shared = outer.shared if outer is not None and outer.shared is not None else {}
    with _bound(_Install(target_dir.resolve(), shared=shared)):
        yield


@contextmanager
def project_bound(target_dir: Path) -> Iterator[None]:
    """Name *target_dir* as the project for the block without making it an install.

    For ``audit`` and ``export``, which read another checkout's runs and learnings. They used to set
    process-wide ``TRW_PROJECT_ROOT`` and reload the shared config, so every other thread in the
    process resolved their target as its own project until they finished (queued row (c) of
    PRD-CORE-305 FR07). Unlike an install, the block's daemon waits keep no budget (an export reads
    the store in full); inside an enclosing install they still draw on that install's.
    """
    outer = _TARGET.get()
    with _bound(_Install(target_dir.resolve(), shared=outer.shared if outer is not None else None)):
        yield


@contextmanager
def _bound(install: _Install) -> Iterator[None]:
    token = _TARGET.set(install)
    try:
        yield
    finally:
        _TARGET.reset(token)


def install_target() -> Path | None:
    """The project an enclosing install is writing into, or ``None`` outside one."""
    bound = _TARGET.get()
    return bound.root if bound is not None else None


def install_cache() -> dict[str, Any] | None:
    """The enclosing install's own cache, or ``None`` outside one."""
    bound = _TARGET.get()
    return bound.cache if bound is not None else None


def install_shared() -> dict[str, Any] | None:
    """State shared across the outermost install and the installs nested in it, or ``None`` outside one."""
    bound = _TARGET.get()
    return bound.shared if bound is not None else None
