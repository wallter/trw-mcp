"""Tests: no AG-03 implementation file exists (audit P0-04).

PRD-DIST-2404 audit P0-04 — AG-03 is manifest-only; the stub file
_before_edit_hook.py MUST NOT be importable as a real module (it raises
NotImplementedError at module level), and MUST exist as a stub-only file
with the appropriate disclaimer comment.

The test_no_ag03_stub.py name per the playbook verifies no live code exists.
"""

from __future__ import annotations

from pathlib import Path


def test_before_edit_hook_file_exists_as_stub() -> None:
    """AG-03: _before_edit_hook.py exists as a stub (not importable — raises)."""
    import importlib.util

    # The file exists (manifest stub).
    spec = importlib.util.find_spec("trw_mcp.channels.antigravity._before_edit_hook")
    # If spec is None the file doesn't exist at all — that would fail the
    # manifest-stub requirement.  If it exists, importing it MUST raise
    # NotImplementedError (that's the stub guard).
    assert spec is not None, (
        "_before_edit_hook.py must exist as a stub file in channels/antigravity/"
    )

    # The file must raise NotImplementedError when imported.
    raised = False
    try:
        import trw_mcp.channels.antigravity._before_edit_hook  # noqa: F401
    except NotImplementedError:
        raised = True

    assert raised, (
        "_before_edit_hook.py must raise NotImplementedError at import time "
        "(AG-03 is manifest-only, no implementation)"
    )


def test_before_edit_hook_stub_has_disclaimer() -> None:
    """AG-03: stub file contains OQ-01 gate comment."""
    import importlib.util

    spec = importlib.util.find_spec("trw_mcp.channels.antigravity._before_edit_hook")
    assert spec is not None
    assert spec.origin is not None

    source = Path(spec.origin).read_text(encoding="utf-8")
    assert "OQ-01" in source, "Stub must reference OQ-01 activation gate"
    assert "DO NOT IMPLEMENT" in source, "Stub must contain DO NOT IMPLEMENT comment"
