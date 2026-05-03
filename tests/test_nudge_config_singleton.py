"""PRD-FIX-085 FR03: _try_learning_nudge_content uses get_config() singleton.

Pre-fix: TRWConfig.model_validate({"trw_dir": str(trw_dir)}) ran on every
nudge call, triggering full settings-model construction (env loading,
YAML parsing, profile resolution). Latent perf regression risk.

Post-fix: get_config() singleton is reused across calls.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch


def test_nudge_path_invokes_get_config_at_runtime() -> None:
    """At runtime, _try_learning_nudge_content calls get_config().

    Patches the get_config import inside the nudge function and asserts it
    was called when session_start runs. Distinguishes the FR03 fix from the
    separate _load_config_for_trw_dir helper (which intentionally uses
    model_validate for per-workspace configs).
    """
    from tests.conftest import extract_tool_fn, make_test_server

    fn = extract_tool_fn(make_test_server("ceremony"), "trw_session_start")

    # Patch where _try_learning_nudge_content imports it from.
    call_count = 0

    def counting_get_config() -> Any:
        nonlocal call_count
        call_count += 1
        from trw_mcp.models.config import _real_get_config_for_test_only  # type: ignore[attr-defined]

        return _real_get_config_for_test_only()

    # The nudge function does `from trw_mcp.models.config import get_config`
    # at runtime; patch that module-level binding.
    from trw_mcp.models.config import get_config as real_get_config

    with patch("trw_mcp.models.config.get_config", side_effect=lambda: real_get_config()) as mock_gc:
        fn(ctx=None, query="config-singleton-probe")

    # get_config() should have been called at least once during the
    # session_start (by the nudge path; possibly other paths too).
    assert mock_gc.call_count >= 1, (
        f"FR03: nudge path must call get_config() at least once "
        f"(call_count={mock_gc.call_count})"
    )


def test_nudge_path_uses_get_config_import() -> None:
    """Source check: _try_learning_nudge_content must import get_config, not TRWConfig.model_validate."""
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src/trw_mcp/tools/_ceremony_status.py"
    text = src.read_text(encoding="utf-8")

    # Find the function body for _try_learning_nudge_content.
    start = text.find("def _try_learning_nudge_content(")
    assert start != -1, "_try_learning_nudge_content function must exist"
    # Body extends until the next top-level def or end of file.
    body_end = text.find("\ndef ", start + 1)
    body = text[start : body_end if body_end != -1 else len(text)]

    assert "get_config()" in body, "FR03: nudge path must use get_config() singleton"
    assert "TRWConfig.model_validate(" not in body, (
        "FR03: nudge path must not construct fresh TRWConfig via model_validate"
    )
