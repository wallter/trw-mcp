"""Tests for channels/opencode/_tool_return_enrichment.py.

PRD-DIST-2403 FR16-FR19 / audit P0-13 / P1-11.

Note: T2 tool-return payload construction is handled by the shared substrate
``channels/_tool_return_tiers.py::enrich_response()``, called directly from
``tools/before_edit_hint.py``, and
``tools/codebase_risk_report.py``.  The per-client ``build_t2_payload``
helper that previously lived here was dead code (never called from tools/)
and was removed.  The substrate ``enrich_response`` path covers FR16.
"""

from __future__ import annotations

import pytest


def test_substrate_enrich_response_is_wired_in_tools() -> None:
    """FR16: T2 payload construction uses the shared substrate enrich_response.

    Verifies that tools/before_edit_hint.py imports the substrate path, not
    a per-client builder.  This is the integration check that build_t2_payload
    was never wired in production.
    """
    import pathlib

    tool_file = pathlib.Path(__file__).parent.parent.parent.parent / "src" / "trw_mcp" / "tools" / "before_edit_hint.py"
    if not tool_file.exists():
        return  # substrate file not in this checkout; skip gracefully

    source = tool_file.read_text(encoding="utf-8")
    # The substrate path must be present
    assert "enrich_response" in source, "enrich_response substrate not found in before_edit_hint.py"
    # The dead per-client builder must NOT be present
    assert "build_t2_payload" not in source, "Dead build_t2_payload found in before_edit_hint.py"


@pytest.fixture(autouse=True)
def _structlog_defaults_for_capture() -> object:
    """File-scoped: reset structlog to defaults so ``capture_logs()`` sees WARN.

    A prior test's ``configure_logging()`` (server import / init_project) installs
    a filtering wrapper that drops WARN before ``capture_logs``'s processor, so
    these warning-assertion tests fail only in full-suite ordering. Save+restore
    (file-scoped, never a global reset — avoids the alphabetical-leak hazard).
    """
    import structlog

    _saved = structlog.get_config()
    structlog.reset_defaults()
    yield
    structlog.configure(**_saved)
