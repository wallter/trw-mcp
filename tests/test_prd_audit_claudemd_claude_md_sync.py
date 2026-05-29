"""Claude MD sync coverage tests split from test_prd_audit_claudemd."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.persistence import FileStateReader


def _run_sync(tmp_path: Path, **kwargs: object) -> dict[str, object]:
    from trw_mcp.state.claude_md import execute_claude_md_sync

    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    (trw_dir / "patterns").mkdir(parents=True, exist_ok=True)

    args: dict[str, object] = {
        "scope": "root",
        "target_dir": None,
        "config": TRWConfig(agents_md_enabled=True, trw_dir=str(trw_dir)),
        "reader": FileStateReader(),
        "llm": MagicMock(available=False),
    }
    args.update(kwargs)

    with (
        patch("trw_mcp.state.claude_md.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.state.claude_md.resolve_project_root", return_value=tmp_path),
    ):
        return execute_claude_md_sync(**args)  # type: ignore[arg-type]


class TestExecuteClaudeMdSyncAgentsMd:
    """Cover lines 733-734, 794: agents_md sync path."""

    def test_agents_md_synced_when_enabled_root_scope(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        _reset_config()

        # FR13: AGENTS.md requires opencode IDE detection
        (tmp_path / ".opencode").mkdir(exist_ok=True)

        result = _run_sync(tmp_path)

        assert result["agents_md_synced"] is True
        assert result["agents_md_path"] is not None
        agents_md = tmp_path / "AGENTS.md"
        assert agents_md.exists()

    def test_agents_md_not_synced_for_sub_scope(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        _reset_config()

        sub_dir = tmp_path / "submodule"
        sub_dir.mkdir()

        result = _run_sync(
            tmp_path,
            scope="sub",
            target_dir=str(sub_dir),
        )

        assert result["agents_md_synced"] is False
        assert result["scope"] == "sub"

    def test_agents_md_not_synced_when_disabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        _reset_config()

        result = _run_sync(
            tmp_path,
            config=TRWConfig(agents_md_enabled=False, trw_dir=str(tmp_path / ".trw")),
        )

        assert result["agents_md_synced"] is False


# =============================================================================
# Additional targeted tests for remaining coverage gaps
# =============================================================================
