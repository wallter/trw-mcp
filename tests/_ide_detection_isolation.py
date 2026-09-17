"""One place to keep ``detect_ide`` inside the temp project, off the host's PATH.

``detect_ide`` (``trw_mcp.bootstrap._utils``) deliberately mixes two signals:
files under the project root, and machine-global ones — ``shutil.which("cursor")``,
``shutil.which("cursor-agent")`` and the ``CURSOR_*`` env vars
(PRD-CORE-136-FR07). That is correct in production: a developer running the
Cursor CLI in a fresh directory should be recognised.

It is wrong for any test that seeds a bare ``tmp_path`` and then asserts what
was written. On a workstation with Cursor installed, every empty ``tmp_path``
detects as ``cursor-cli`` + ``cursor-ide``, whose profiles do not claim
CLAUDE.md, so the sync correctly writes nothing and the test fails on the
MACHINE rather than on the code. Confirmed 2026-09-17: eight trw-mcp test
files went green purely by removing ``cursor``/``cursor-agent`` from PATH.

Use this helper in tests whose subject is "what does a project with these
files produce"; do NOT use it in tests whose subject IS the machine-global
detection (``test_bootstrap_ide_detection.py``), which must keep seeing PATH.

``trw_mcp.state.claude_md._agents_md.detect_ide`` delegates to the same
function, so patching ``_utils.shutil`` covers both entry points.
"""

from __future__ import annotations

from typing import Any

import pytest


def isolate_ide_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``detect_ide`` depend only on the target directory's contents."""
    import shutil

    from trw_mcp.bootstrap import _utils

    real_which = shutil.which

    def _which(cmd: str, *args: Any, **kwargs: Any) -> str | None:
        if cmd in ("cursor", "cursor-agent"):
            return None
        return real_which(cmd, *args, **kwargs)

    monkeypatch.setattr(_utils.shutil, "which", _which)
    for name in ("CURSOR_TRACE_ID", "CURSOR_SESSION_ID", "CURSOR_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def ide_detection_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture form of :func:`isolate_ide_detection`, for explicit opt-in."""
    isolate_ide_detection(monkeypatch)
