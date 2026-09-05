"""Process-wide ``.trw`` path isolation for the trw-mcp test suite.

Why this exists
---------------
``_isolate_trw_dir`` (conftest) used to hand-enumerate the modules whose
``resolve_trw_dir`` / ``resolve_project_root`` aliases it patched. That list is
structurally unable to be correct:

* 24 modules under ``src/trw_mcp/`` bind one of those two names at import time
  via ``from trw_mcp.state._paths import ...``. The enumerated list named 9.
  ``trw_mcp.telemetry.pipeline`` was one of the 15 that were missing, which is
  how the test suite wrote ~8k synthetic events into the REAL
  ``.trw/logs/pipeline-events.jsonl`` — a file ``trw-eval`` reads for RCA
  scoring (``trw_eval/analysis/_run_facts_extract.py``).
* ``monkeypatch`` is function-scoped, so every per-test patch is reverted at
  teardown. Any background thread that outlives its test (the telemetry flush
  timer) resolves paths *after* the revert and therefore lands on the real repo.

Both failure modes come from the same root cause: **per-importer patching of a
snapshot value.** The fix is to make the isolation a *stable indirection*
instead of a snapshot:

1. ``resolve_trw_dir`` / ``resolve_project_root`` here are permanent stand-ins
   that read :func:`current_root` at call time.
2. :func:`install` sweeps ``sys.modules`` and rebinds every ``trw_mcp`` alias
   still pointing at a genuine resolver. No module list to maintain — a module
   added tomorrow is covered the first time it is imported.
3. Because the stand-ins are never uninstalled, a thread that fires between (or
   after) tests still resolves to a temp directory rather than the real repo.

The install is deliberately *not* done through ``monkeypatch``: restoring the
genuine resolver at teardown is precisely what re-opens the leaked-thread hole.

``tests/test_trw_dir_isolation_guard.py`` is the runtime guard that fails if any
module escapes this, so correctness no longer depends on a grep or a list.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import trw_mcp.state._paths as _real_paths

# Captured at import of this module — before any fixture rebinding — so the
# sweep can recognise a genuine resolver by identity rather than by name.
REAL_RESOLVE_TRW_DIR: Callable[[], Path] = _real_paths.resolve_trw_dir
REAL_RESOLVE_PROJECT_ROOT: Callable[[], Path] = _real_paths.resolve_project_root

_current_root: Path | None = None
_quarantine: Path | None = None


def _quarantine_root() -> Path:
    """Return (creating once) a scratch root used outside any active test.

    Collection-time and post-session resolution has no ``tmp_path`` to aim at.
    Sending it to a throwaway directory keeps the real repo untouched without
    having to make resolution fail (production code is fail-open, so a raise
    would be swallowed and prove nothing).
    """
    global _quarantine
    if _quarantine is None:
        _quarantine = Path(tempfile.mkdtemp(prefix="trw-test-quarantine-"))
    return _quarantine


def set_current_root(root: Path) -> None:
    """Point every isolated resolver at *root* for the duration of a test."""
    global _current_root
    _current_root = root


def current_root() -> Path:
    """Return the project root all isolated resolvers currently report."""
    return _current_root if _current_root is not None else _quarantine_root()


def resolve_project_root() -> Path:
    """Isolated stand-in for ``trw_mcp.state._paths.resolve_project_root``.

    Mirrors the genuine function's ``TRW_PROJECT_ROOT``-env-var-first
    precedence instead of unconditionally returning the fixture's tmp dir.

    The stand-in used to always return :func:`current_root`, silently
    ignoring ``TRW_PROJECT_ROOT`` even when a test set it explicitly to
    verify env-var-based resolution. That leaked the isolation harness's
    own tmp-path choice into tests whose whole point was to exercise the
    env-var branch, which then passed or failed for the wrong reason (see
    ``tests/test_core205_review_producers_enforce.py`` for a test that
    routed around this by rooting its fixture at ``tmp_path`` itself). A
    test-set ``TRW_PROJECT_ROOT`` is still confined to whatever directory
    the test created (almost always under ``tmp_path``), so honoring it
    does not reopen the real-repo leak this module exists to close.
    """
    env_root = os.environ.get("TRW_PROJECT_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return current_root()


def resolve_trw_dir() -> Path:
    """Isolated stand-in for ``trw_mcp.state._paths.resolve_trw_dir``."""
    return current_root() / ".trw"


# (attribute name, genuine resolver, isolated stand-in)
_SUBSTITUTIONS: tuple[tuple[str, Callable[[], Path], Callable[[], Path]], ...] = (
    ("resolve_trw_dir", REAL_RESOLVE_TRW_DIR, resolve_trw_dir),
    ("resolve_project_root", REAL_RESOLVE_PROJECT_ROOT, resolve_project_root),
)


def _trw_mcp_modules() -> list[tuple[str, object]]:
    """Return every currently-imported ``trw_mcp`` module as ``(name, module)``."""
    return [
        (name, module)
        for name, module in list(sys.modules.items())
        if module is not None and (name == "trw_mcp" or name.startswith("trw_mcp."))
    ]


def install() -> int:
    """Rebind every genuine resolver alias in ``trw_mcp`` to the isolated one.

    Idempotent and cheap: an alias already pointing at the stand-in is skipped,
    so re-running per test only pays for modules imported since the last sweep.

    Returns:
        Number of aliases rebound by this call.
    """
    rebound = 0
    for _name, module in _trw_mcp_modules():
        for attr, real, isolated in _SUBSTITUTIONS:
            if getattr(module, attr, None) is real:
                setattr(module, attr, isolated)
                rebound += 1
    return rebound


def unisolated_aliases() -> list[str]:
    """Return ``module:attr`` for every alias still bound to a genuine resolver.

    The runtime guard asserts this is empty. A non-empty result names exactly
    the modules whose writes would land in the real ``.trw/``.
    """
    escaped: list[str] = []
    for name, module in _trw_mcp_modules():
        for attr, real, _isolated in _SUBSTITUTIONS:
            if getattr(module, attr, None) is real:
                escaped.append(f"{name}:{attr}")
    return sorted(escaped)


def isolated_alias_count() -> int:
    """Return how many ``trw_mcp`` aliases currently point at a stand-in.

    Used as the non-vacuity control for the guard: an empty
    :func:`unisolated_aliases` means nothing only if this is non-zero.
    """
    isolated_fns = {isolated for _attr, _real, isolated in _SUBSTITUTIONS}
    return sum(
        1
        for _name, module in _trw_mcp_modules()
        for attr, _real, _isolated in _SUBSTITUTIONS
        if getattr(module, attr, None) in isolated_fns
    )
