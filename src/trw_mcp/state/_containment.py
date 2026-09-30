"""The one containment rule for TRW state writes (E2E-INC-034/035).

``.trw`` is partly tracked in git, so a hostile checkout can ship ``.trw/learnings/entries -> /somewhere`` or a
symlinked run directory, and every writer that ``mkdir``s and opens by path then writes TRW state outside the
project. The leaf guards (``safe_write`` / ``symlink_leaf``) only looked at the last component.

:func:`assert_trw_write_contained` is called before any TRW state write, append or directory creation. For a
path under a ``.trw`` directory it requires that no component from that ``.trw`` down to the target (the
target included when it exists) is a symlink, and that there is no ``..`` below it. ``.trw`` itself is checked
too, except the configured user-scope directory (``TRW_USER_DIR`` or its default ``~/.trw``) when it is not this
project's trw_dir: a user may place that store elsewhere on purpose, which is not a checkout's choice. A path under no ``.trw`` is not TRW state and is not this rule's business.

The walk anchors on the OUTERMOST ``.trw`` (self-review, r2): anchoring on the innermost let a path like
``.trw/runs/<planted link>/.trw/...`` skip the link. A worktree's ``<repo>/.trw/worktrees/<w>/.trw`` is walked from
the main ``.trw`` down, through real directories.

It is an ``lstat`` walk, not an ``O_NOFOLLOW`` open, so a same-user writer racing the walk is out of scope
(the same threat model as the rest of ``state/``); it closes the planted-symlink class a checkout can ship.
"""

from __future__ import annotations

from pathlib import Path

from trw_memory.exceptions import UnsafeWriteError

from trw_mcp.exceptions import StateError

__all__ = ["ContainmentError", "assert_trw_write_contained", "trw_write_contained"]


class ContainmentError(StateError, UnsafeWriteError):
    """A TRW state write would leave the project through a symlink or ``..``.

    Also an ``UnsafeWriteError``, so every handler written for a refused safe_fs write (deliver's outcome record,
    handoff seal, ...) treats a containment refusal the same way instead of letting it escape.
    """

    def __init__(self, message: str, *, path: str = "") -> None:
        super().__init__(message, path=path)  # TRWError keeps path in context; UnsafeWriteError's init resets it
        self.path = path
        self.reason = "trw_containment"


def assert_trw_write_contained(path: Path) -> None:
    """Refuse a TRW state write whose path crosses a symlink (or ``..``) at or below its ``.trw`` directory."""
    parts = Path(path).absolute().parts  # absolute() keeps "..", which the check below must see
    if ".trw" not in parts:
        return
    anchor = parts.index(".trw")  # the OUTERMOST .trw: a nested ".trw" below a planted link must not reset the walk
    if ".." in parts[anchor:]:
        raise ContainmentError("refusing a TRW state path with '..' below .trw", path=str(path))
    current = Path(*parts[:anchor])
    for index, part in enumerate(parts[anchor:]):
        current = current / part
        if not current.is_symlink():
            continue
        if index == 0 and _is_user_scope_only(current):
            continue  # the user's own user-scope store, placed wherever they chose
        raise ContainmentError(
            f"refusing to write TRW state through a symlink: {current} is a symlink (a checkout can plant "
            "one). Replace that symlink with a real directory (remove the link, then mkdir it) and retry.",
            path=str(path),
        )


def _is_user_scope_only(trw_root: Path) -> bool:
    """Is *trw_root* the configured USER-scope directory, and NOT this project's trw_dir? (codex r1 P0, lead ruling)

    A scope check, not a location heuristic: when the project root IS ``$HOME`` (a dotfiles repo, a tmp-HOME
    sandbox) the user dir and the project's trw_dir are the same directory, which is project state a checkout can
    ship, so it is contained. Only evaluated for a ``.trw`` that is a symlink; anything unresolvable is not exempt.
    """
    try:
        from trw_memory.user_paths import user_memory_dir_path

        user_root = user_memory_dir_path().parent.resolve()  # follows links; compares only, never chmods the store
    except Exception:  # trw-fail-silent-allow: fail CLOSED -- an unresolvable user scope exempts nothing
        return False
    try:
        if trw_root.resolve() != user_root:
            return False
    except (OSError, RuntimeError):  # trw-fail-silent-allow: a symlink loop (3.11/3.12 raise) is a refusal, not a crash
        return False
    try:
        from trw_mcp.state._paths import resolve_trw_dir

        project_trw = resolve_trw_dir().resolve()
    except Exception:  # trw-fail-silent-allow: fail CLOSED -- an unresolvable project could be rooted at $HOME
        return False
    return project_trw != user_root


def trw_write_contained(path: Path) -> bool:
    """Boolean form for best-effort appenders: ``False`` (and a warning) where :func:`assert_trw_write_contained`
    would raise, so the caller skips that write instead of crashing its sweep."""
    try:
        assert_trw_write_contained(path)
    except ContainmentError as exc:
        key = str(path)
        if key not in _WARNED:  # once per path per process: a planted link is visible without flooding the log
            _WARNED.add(key)
            import structlog

            structlog.get_logger(__name__).warning("trw_state_write_refused", path=key, reason=str(exc))
        return False
    return True


#: Paths already reported by :func:`trw_write_contained` in this process.
_WARNED: set[str] = set()
