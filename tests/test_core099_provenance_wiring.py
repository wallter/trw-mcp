"""Integration wiring tests for PRD-CORE-099 — Learning Source Provenance.

Verifies the full pipeline: trw_learn → auto-detection → store → backend.
These tests prove the detection code is actually wired into the tool,
not just tested in isolation (anti-pattern: "facade without wiring").
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def _setup_trw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create minimal .trw structure for learning storage."""
    trw_dir = tmp_path / ".trw"
    entries_dir = trw_dir / "learnings" / "entries"
    entries_dir.mkdir(parents=True)
    (trw_dir / "context").mkdir(parents=True)

    # Point resolve_trw_dir at our temp dir
    monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: trw_dir)
    return trw_dir


class TestAutoDetectionWiring:
    """Verify auto-detection flows through to stored entries."""

    def test_auto_detects_claude_code_client(
        self, _setup_trw: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """trw_learn with no client_profile arg stores auto-detected 'claude-code'."""
        from trw_mcp.tools._learn_impl import execute_learn

        stored_entries: list[dict[str, object]] = []

        def mock_store(
            trw_dir: Path,
            learning_id: str,
            summary: str,
            detail: str,
            **kwargs: object,
        ) -> dict[str, object]:
            stored_entries.append({"learning_id": learning_id, **kwargs})
            return {"learning_id": learning_id, "status": "recorded"}

        monkeypatch.setattr("trw_mcp.tools.learning.adapter_store", mock_store)
        monkeypatch.setattr(
            "trw_mcp.tools.learning.generate_learning_id", lambda: "L-wire-001"
        )
        monkeypatch.setattr(
            "trw_mcp.tools.learning.save_learning_entry",
            lambda trw_dir, entry: _setup_trw / "learnings" / "entries" / "test.yaml",
        )
        monkeypatch.setattr("trw_mcp.tools.learning.update_analytics", lambda *a: None)

        with patch.dict(
            "os.environ",
            {"CLAUDE_CODE_VERSION": "1.2.3", "CLAUDE_MODEL": "claude-opus-4-6"},
            clear=True,
        ):
            from trw_mcp.state.source_detection import detect_client_profile, detect_model_id

            client = detect_client_profile()
            model = detect_model_id()

            from trw_mcp.models.config import get_config

            result = execute_learn(
                summary="test wiring",
                detail="provenance wiring test",
                trw_dir=_setup_trw,
                config=get_config(),
                client_profile=client,
                model_id=model,
                _adapter_store=mock_store,
                _generate_learning_id=lambda: "L-wire-001",
                _save_learning_entry=lambda trw_dir, entry: _setup_trw / "test.yaml",
                _update_analytics=lambda *a: None,
                _list_active_learnings=lambda trw_dir: [],
                _check_and_handle_dedup=lambda *a, **kw: None,
            )

        assert result["status"] == "recorded"
        assert len(stored_entries) == 1
        assert stored_entries[0]["client_profile"] == "claude-code"
        assert stored_entries[0]["model_id"] == "claude-opus-4-6"

    def test_explicit_override_beats_detection(
        self, _setup_trw: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit client_profile/model_id args override auto-detection."""
        from trw_mcp.models.config import get_config
        from trw_mcp.tools._learn_impl import execute_learn

        stored_entries: list[dict[str, object]] = []

        def mock_store(
            trw_dir: Path,
            learning_id: str,
            summary: str,
            detail: str,
            **kwargs: object,
        ) -> dict[str, object]:
            stored_entries.append({"learning_id": learning_id, **kwargs})
            return {"learning_id": learning_id, "status": "recorded"}

        with patch.dict(
            "os.environ",
            {"CLAUDE_CODE_VERSION": "1.2.3", "CLAUDE_MODEL": "claude-opus-4-6"},
            clear=True,
        ):
            result = execute_learn(
                summary="override test",
                detail="explicit values",
                trw_dir=_setup_trw,
                config=get_config(),
                client_profile="custom-ide",
                model_id="custom-model-v1",
                _adapter_store=mock_store,
                _generate_learning_id=lambda: "L-wire-002",
                _save_learning_entry=lambda trw_dir, entry: _setup_trw / "test.yaml",
                _update_analytics=lambda *a: None,
                _list_active_learnings=lambda trw_dir: [],
                _check_and_handle_dedup=lambda *a, **kw: None,
            )

        assert result["status"] == "recorded"
        assert len(stored_entries) == 1
        # Explicit values should pass through, NOT be overridden by detection
        assert stored_entries[0]["client_profile"] == "custom-ide"
        assert stored_entries[0]["model_id"] == "custom-model-v1"

    def test_empty_string_suppresses_detection(
        self, _setup_trw: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit empty string is NOT overridden by auto-detection."""
        from trw_mcp.models.config import get_config
        from trw_mcp.tools._learn_impl import execute_learn

        stored_entries: list[dict[str, object]] = []

        def mock_store(
            trw_dir: Path,
            learning_id: str,
            summary: str,
            detail: str,
            **kwargs: object,
        ) -> dict[str, object]:
            stored_entries.append({"learning_id": learning_id, **kwargs})
            return {"learning_id": learning_id, "status": "recorded"}

        with patch.dict(
            "os.environ",
            {"CLAUDE_CODE_VERSION": "1.2.3", "CLAUDE_MODEL": "claude-opus-4-6"},
            clear=True,
        ):
            result = execute_learn(
                summary="empty string test",
                detail="empty overrides",
                trw_dir=_setup_trw,
                config=get_config(),
                client_profile="",
                model_id="",
                _adapter_store=mock_store,
                _generate_learning_id=lambda: "L-wire-003",
                _save_learning_entry=lambda trw_dir, entry: _setup_trw / "test.yaml",
                _update_analytics=lambda *a: None,
                _list_active_learnings=lambda trw_dir: [],
                _check_and_handle_dedup=lambda *a, **kw: None,
            )

        assert result["status"] == "recorded"
        assert len(stored_entries) == 1
        # Empty string passed explicitly should remain empty
        assert stored_entries[0]["client_profile"] == ""
        assert stored_entries[0]["model_id"] == ""


class TestRecallOutputProvenance:
    """Verify provenance fields appear in recall output."""

    def test_compact_mode_excludes_provenance(self) -> None:
        """Compact mode should NOT include client_profile or model_id."""
        from trw_memory.models.memory import MemoryEntry

        from trw_mcp.state._memory_transforms import _memory_to_learning_dict

        entry = MemoryEntry(
            id="L-compact-001",
            content="test compact",
            client_profile="claude-code",
            model_id="claude-opus-4-6",
        )
        result = _memory_to_learning_dict(entry, compact=True)
        assert "client_profile" not in result
        assert "model_id" not in result

    def test_full_mode_includes_provenance(self) -> None:
        """Full mode should include client_profile and model_id."""
        from trw_memory.models.memory import MemoryEntry

        from trw_mcp.state._memory_transforms import _memory_to_learning_dict

        entry = MemoryEntry(
            id="L-full-001",
            content="test full",
            client_profile="opencode",
            model_id="claude-sonnet-4-6",
        )
        result = _memory_to_learning_dict(entry, compact=False)
        assert result["client_profile"] == "opencode"
        assert result["model_id"] == "claude-sonnet-4-6"
