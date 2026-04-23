"""PRD-CORE-149-FR11: profile dispatcher unit tests.

Verifies that ``dispatch_for_profile`` routes for every built-in profile
(n=8) and gracefully falls back for an unknown client identifier.

The dispatcher internally delegates to ``_determine_write_target_decision``
for the client-targeting logic; the assertions here stay at the
dispatcher boundary so these tests do not duplicate the deeper coverage
in ``test_agents_md.py`` / ``test_per_client_instructions.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md._profile_dispatcher import dispatch_for_profile
from trw_mcp.state.claude_md._sync import execute_claude_md_sync

pytestmark = pytest.mark.integration

_BUILTIN_CLIENTS = [
    "auto",
    "claude-code",
    "opencode",
    "cursor",
    "codex",
    "aider",
    "gemini",
    "copilot",
    "all",
]


@pytest.fixture()
def _sync_env(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point sync helpers at ``tmp_project``."""
    trw_dir = tmp_project / ".trw"
    trw_dir.mkdir(exist_ok=True)
    (trw_dir / "context").mkdir(exist_ok=True)
    monkeypatch.setattr(
        "trw_mcp.state.claude_md.resolve_project_root",
        lambda: tmp_project,
    )
    monkeypatch.setattr(
        "trw_mcp.state.claude_md.resolve_trw_dir",
        lambda: trw_dir,
    )
    return tmp_project


@pytest.mark.parametrize("client", _BUILTIN_CLIENTS)
def test_dispatch_for_each_builtin_profile(client: str, _sync_env: Path) -> None:
    """Dispatcher returns a structurally-valid result for every built-in client."""
    result = dispatch_for_profile(
        scope="root",
        target_dir=None,
        config=TRWConfig(),
        reader=cast("object", MagicMock()),  # dispatcher ignores reader; MagicMock satisfies signature
        llm=cast("object", MagicMock()),
        client=client,
    )
    assert "status" in result
    assert result["status"] in {"synced", "unchanged"}
    assert "path" in result
    assert "scope" in result and result["scope"] == "root"


def test_unknown_profile_falls_back_to_claude_code(_sync_env: Path) -> None:
    """Unknown client ids fall through the same decision path as claude-code.

    The dispatcher does not crash on unrecognised client strings; it reports
    a ``synced`` / ``unchanged`` outcome and writes through the
    write-target decision helper (which already falls back to the
    ``claude-code`` behaviour for unknown client identifiers).
    """
    result = dispatch_for_profile(
        scope="root",
        target_dir=None,
        config=TRWConfig(),
        reader=cast("object", MagicMock()),
        llm=cast("object", MagicMock()),
        client="totally-unknown-client",
    )
    assert result["status"] in {"synced", "unchanged"}


def test_sync_facade_is_dispatcher_alias(_sync_env: Path) -> None:
    """``execute_claude_md_sync`` delegates to ``dispatch_for_profile``."""
    dispatched = dispatch_for_profile(
        scope="root",
        target_dir=None,
        config=TRWConfig(),
        reader=cast("object", MagicMock()),
        llm=cast("object", MagicMock()),
        client="claude-code",
    )
    via_facade = execute_claude_md_sync(
        scope="root",
        target_dir=None,
        config=TRWConfig(),
        reader=cast("object", MagicMock()),
        llm=cast("object", MagicMock()),
        client="claude-code",
    )
    # Both code paths produce ClaudeMdSyncResultDict-shaped output with the
    # same mandatory keys. The second call may add the optional ``hash`` key
    # because the first call primed the content-hash cache.
    required_keys = {"status", "path", "scope", "total_lines", "review_md"}
    assert required_keys.issubset(dispatched.keys())
    assert required_keys.issubset(via_facade.keys())
    assert dispatched["scope"] == via_facade["scope"] == "root"
