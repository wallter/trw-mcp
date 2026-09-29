"""Reusing an existing ``venv-<version>``: bring trw-distill in without ever breaking a working venv."""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

from packaging.version import InvalidVersion, Version

from trw_mcp.shared_server._records import SharedServerError, validate_env

_INSTALL_ERRORS = (SharedServerError, OSError, subprocess.SubprocessError)
_DIST_VERSION = "import importlib.metadata as m; print(m.version('trw-distill'))"


def _dist_version(python: Path) -> str | None:
    from trw_mcp.shared_server import _ops

    try:
        return _ops._run([str(python), "-c", _DIST_VERSION], env=_ops._probe_env()) or None
    except _INSTALL_ERRORS:  # trw-fail-silent-allow: a failed metadata probe IS the answer (not installed)
        return None


def _same_version(installed: str | None, wanted: str) -> bool:
    """``1.0`` equals ``1.0.0`` (PEP 440); an unparseable side compares as text."""
    if installed is None:
        return False
    try:
        return Version(installed) == Version(wanted)
    except InvalidVersion:  # trw-fail-silent-allow: an unorderable version is compared as text, never guessed
        return installed == wanted


def _newer(wanted: str, installed: str | None) -> bool:
    """Is *wanted* strictly newer than *installed*? Unknown or unparseable installed -> True; unparseable wanted -> False."""
    if installed is None:
        return True  # nothing to compare against: the auto-selected wheel is the best available
    try:
        return Version(wanted) > Version(installed)
    except InvalidVersion:  # trw-fail-silent-allow: an unorderable auto wheel never replaces a working venv
        return False


def fork_current(python: Path, wanted: str) -> bool:
    """Does the fork at *python* import trw_mcp AND carry trw-distill == *wanted*?"""
    from trw_mcp.shared_server import _ops

    return _ops._importable(python, "trw_mcp") and _same_version(_dist_version(python), wanted)


def fork_tag(wanted: str) -> str:
    """*wanted* as a venv-name tag: lowercased, other characters as ``.``; a hash of the original when that changed it."""
    lowered = wanted.lower()
    tag = re.sub(r"[^a-z0-9._-]", ".", lowered)
    if tag != wanted:  # anything altered could collide with another version's tag (1+2 vs 1.2)
        tag = f"{tag}-{hashlib.sha256(wanted.encode()).hexdigest()[:8]}"
    return tag


def forked_venv(venv: Path, spec: str | None, *, pinned: bool) -> Path | None:
    """``venv-<V>+distill-<Y>`` beside *venv* when *venv* has trw-distill that *spec* should replace; else ``None``.

    An explicit ``--with`` pin (*pinned*) forks on any difference; an auto-selected wheel only when strictly
    newer than the installed one (a plain swap never downgrades a venv built with a newer pin), or when the
    installed version cannot be read. Nothing here writes: the fork path is only named. An absent distill is not
    a fork (it installs in place).
    """
    from trw_mcp.shared_server import _ops

    python = venv / "bin" / "python"
    if spec is None or not _ops._importable(python, "trw_distill"):
        return None
    wanted = spec.partition("==")[2]
    installed = _dist_version(python)
    if _same_version(installed, wanted) or not (pinned or _newer(wanted, installed)):
        return None
    tag = fork_tag(wanted)
    name = f"{venv.name.removeprefix('venv-')}.distill-{tag}"  # the joined name, as validate_env sees it
    try:
        validate_env(name)
    except SharedServerError as exc:
        raise SharedServerError(
            f"cannot name a fresh venv for trw-distill=={wanted}: {name!r} is not a valid venv name "
            f"(<=32 chars of [a-z0-9._-]); {venv} untouched, nothing swapped"
        ) from exc
    return venv.with_name(f"{venv.name}+distill-{tag}")


def reuse_venv(venv: Path, wheelhouse: Path, spec: str | None, *, pinned: bool) -> None:
    """Install *spec* (trw-distill) into the reused *venv* when it has none; report.

    Only an ABSENT distill is installed in place (no server can be using a package that is not there); a present
    one at another version is ``forked_venv``'s business. Never leaves the venv worse: a failed install (uv
    error, timeout, uv missing) is reported on stderr (an explicit ``--with`` pin then refuses the swap; an
    auto-selected wheel does not), a partial trw-distill is uninstalled best-effort, and the swap continues on
    the still-usable venv.
    """
    from trw_mcp.shared_server import _ops

    python = venv / "bin" / "python"
    has = _ops._importable(python, "trw_distill")
    if spec is not None and not has:
        try:
            _ops._pip_install(python, wheelhouse, [spec])
        except _INSTALL_ERRORS as exc:  # trw-fail-silent-allow: reported on stderr; the venv stays usable
            version = _dist_version(python)
            if version is not None and not _ops._importable(python, "trw_distill"):
                try:  # metadata but no import: a partial install; never leave it half-there
                    _ops._run(["uv", "pip", "uninstall", "--python", str(python), "trw-distill"], env=_ops._probe_env())
                    version = None
                except _INSTALL_ERRORS:  # trw-fail-silent-allow: best-effort cleanup; the state is reported below
                    pass
            state = f"present v{version}" if version else "not installed"
            message = f"install of {spec} failed: {type(exc).__name__}; trw-distill {state} ({exc})"
            print(message, file=sys.stderr)
            if pinned:  # the operator asked for this exact pin: refuse the swap, the venv stays as it was
                raise SharedServerError(f"{message}; {venv} kept, nothing swapped") from exc
        has = _ops._importable(python, "trw_distill")
    print(f"reusing {venv}: trw-distill {'is' if has else 'is NOT'} installed", file=sys.stderr)
