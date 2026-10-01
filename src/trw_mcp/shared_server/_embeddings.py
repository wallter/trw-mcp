"""Embeddings for a shared env's venv: install them, probe for them, and say how to fix their absence (SWAP-VENV-EXTRAS).

Belongs to the ``shared_server`` package (``_ops.build_version_venv`` installs through it, ``_doctor`` and
``_autoswap.surface_block`` report through it). 2026-09-30, first stable hot-swap: the version venv held trw-mcp's
base dependencies only, so with no sentence-transformers every session's recall silently fell back to keyword
ranking. A venv without the extra is now refused at swap time (naming what to fetch), and a running env without
it is flagged by doctor and ``trw_status(detail="surface")`` with the one command that fixes it.

The extra is installed at the trw-memory version the venv ALREADY has, never a newer one: a reused venv may be
the one a server is serving from.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path

import structlog

from trw_mcp.shared_server._records import SharedServerError

logger = structlog.get_logger(__name__)

_PROBE_FIND = "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('sentence_transformers') else 1)"
_PROBE_MEMORY = "from trw_memory._version import __version__; print(__version__)"
_PROBE_ERRORS = (SharedServerError, OSError, subprocess.SubprocessError)
# uv's offline resolver names what it could not find: "Because sentence-transformers was not found in the cache"
# ("Because there is no version of X==1 and you require X==1", "Because torch>=2 needs to be downloaded from a registry").
_MISSING = re.compile(
    r"Because (?:there is no version of )?([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?(?:[=<>!~]=?\S*)? "
    r"(?:was not found|has no (?:matching|wheels)|needs to be downloaded from a registry|and you require)"
)


def memory_version(python: str | None = None, pythonpath: str | None = None) -> str | None:
    """The trw-memory version *python* (default: this process) runs, or ``None`` when it cannot be read."""
    if python is None:
        from trw_memory._version import __version__

        return str(__version__)
    from trw_mcp.shared_server import _ops

    try:
        return _ops._run([python, "-c", _PROBE_MEMORY], env=_ops._probe_env(pythonpath)) or None
    except _PROBE_ERRORS:  # trw-fail-silent-allow: an unreadable version only makes the fix command unpinned
        return None


def embeddings_present(python: str, pythonpath: str | None = None) -> bool:
    """Does *python* find ``sentence_transformers``? ``find_spec`` only: never an import of torch."""
    from trw_mcp.shared_server import _ops

    try:
        _ops._run([python, "-c", _PROBE_FIND], env=_ops._probe_env(pythonpath))
    except _PROBE_ERRORS:  # trw-fail-silent-allow: a failed (or unrunnable) probe IS this probe's answer
        return False
    return True


def spec(version: str | None) -> str:
    """``trw-memory[all]==V`` (the extra at the installed version), unpinned only when the version is unknown."""
    return f"trw-memory[all]=={version}" if version else "trw-memory[all]"


def install(python: Path, wheelhouse: Path) -> None:
    """Give the venv at *python* the embeddings extra when it lacks it; refuse (naming the fetch) when that fails."""
    from trw_mcp.shared_server import _ops

    if embeddings_present(str(python)):
        return
    wanted = spec(memory_version(str(python)))
    try:
        _ops._pip_install(python, wheelhouse, [wanted])
    except _PROBE_ERRORS as exc:
        raise SharedServerError(_failure(wanted, wheelhouse, str(exc))) from exc


def _failure(wanted: str, wheelhouse: Path, detail: str) -> str:
    """Refusal text: what the offline resolver could not find, how to fetch it, and the deliberate way out."""
    missing = ", ".join(dict.fromkeys(_MISSING.findall(detail)))
    cause = f"missing: {missing}" if missing else f"uv said: {detail[-300:]}"
    return (
        f"embeddings are unavailable: `{wanted}` could not be installed offline ({cause}). Recall would silently "
        f"fall back to keyword ranking, so nothing was swapped. Fetch the wheels once with network access: "
        f"`python -m pip download {shlex.quote(wanted)} -d {shlex.quote(str(wheelhouse))}` (run it with the Python "
        f"`uv python find` reports, "
        f"so the wheels match the venv), then retry the swap. To serve keyword-only on purpose, set "
        f"`embeddings_enabled: false` in .trw/config.yaml."
    )


def fix_command(env: str, python: str, version: str | None) -> str:
    """The two commands that give a running env its embeddings."""
    return f"uv pip install --python {shlex.quote(python)} {shlex.quote(spec(version))}, then trw-mcp swap --env {env} --daemon"


def doctor_problem(env: str, python: str, pythonpath: str | None) -> str | None:
    """The ``trw-mcp doctor`` finding for *env*'s interpreter when it lacks the embeddings extra, else ``None``."""
    if embeddings_present(python, pythonpath):
        return None
    fix = fix_command(env, python, memory_version(python, pythonpath))
    return (
        f"{env}: embeddings unavailable ({python} has no sentence-transformers, so recall is keyword-only); fix: {fix}"
    )


def surface_note(env: str) -> str | None:
    """``trw_status(detail="surface")``: this shared server's own embeddings gap, or ``None`` (present, or switched off)."""
    try:  # justified: boundary -- a status extra must never cost the caller the rest of the surface
        from trw_mcp.models.config import get_config

        if not get_config().embeddings_enabled or find_spec("sentence_transformers") is not None:
            return None
        return (
            f"embeddings unavailable: this server's interpreter has no sentence-transformers, so recall is "
            f"keyword-only; fix: {fix_command(env, sys.executable, memory_version())}"
        )
    except Exception:  # trw-fail-silent-allow: logged at debug; a status extra is absent, never an error value
        logger.debug("embeddings_surface_note_failed", exc_info=True)
        return None
