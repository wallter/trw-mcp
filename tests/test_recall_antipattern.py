"""Tests for R-06: Anti-pattern alert in recall.

Covers:
  - Anti-pattern alert surfaces for model/system task queries
  - Anti-pattern alert skipped for unrelated task queries
  - Fail-open behavior with bad data
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_learning(
    lid: str,
    summary: str,
    impact: float = 0.8,
) -> dict[str, object]:
    """Build a minimal learning dict matching the recall result shape."""
    return {"id": lid, "summary": summary, "impact": impact}


def _make_config(**overrides: Any) -> TRWConfig:
    """Build a TRWConfig with sensible defaults for recall tests."""
    defaults: dict[str, Any] = {
        "recall_max_results": 10,
        "ceremony_mode": "full",
    }
    defaults.update(overrides)
    return TRWConfig(**defaults)


def _patch_recall_deps(
    monkeypatch: pytest.MonkeyPatch,
    recall_fn: Any,
) -> None:
    """Patch memory_adapter recall + access tracking at the source module.

    Function-local imports in perform_session_recalls() resolve from the source
    module, so patches must target trw_mcp.state.memory_adapter.
    """
    monkeypatch.setattr(
        "trw_mcp.state.memory_adapter.recall_learnings",
        recall_fn,
    )
    monkeypatch.setattr(
        "trw_mcp.state.memory_adapter.update_access_tracking",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "trw_mcp.state.receipts.log_recall_receipt",
        lambda *a, **kw: None,
    )


# ---------------------------------------------------------------------------
# Tests: anti-pattern alert surfaces for model/system tasks
# ---------------------------------------------------------------------------


class TestAntipatternAlertSurfaces:
    """When query suggests model/system work AND learning has anti-pattern keyword, alert is prepended."""

    def test_facade_keyword_with_model_query(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.tools._ceremony_helpers import perform_session_recalls

        facade_learning = _make_learning("L001", "facade pattern found in adapter layer")
        normal_learning = _make_learning("L002", "pytest fixtures are scoped per-function")

        def mock_recall(
            trw_dir: Path,
            query: str = "*",
            **kwargs: Any,
        ) -> list[dict[str, object]]:
            if query != "*":
                return [facade_learning, normal_learning]
            return [normal_learning]

        _patch_recall_deps(monkeypatch, mock_recall)

        config = _make_config()
        reader = FileStateReader()

        learnings, _, _ = perform_session_recalls(
            trw_dir=tmp_path,
            query="client profile model system",
            config=config,
            reader=reader,
        )

        # The facade learning should have the alert prefix
        facade_entries = [
            e for e in learnings if "ANTI-PATTERN ALERT" in str(e.get("summary", ""))
        ]
        assert len(facade_entries) >= 1, f"Expected at least one alert entry, got {learnings}"

        # The normal learning should NOT have the alert prefix
        normal_entries = [
            e
            for e in learnings
            if "pytest" in str(e.get("summary", ""))
            and "ANTI-PATTERN ALERT" not in str(e.get("summary", ""))
        ]
        assert len(normal_entries) >= 1

    def test_wiring_gap_keyword_with_adapter_query(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.tools._ceremony_helpers import perform_session_recalls

        gap_learning = _make_learning("L003", "wiring gap in registry initialization")

        def mock_recall(
            trw_dir: Path,
            query: str = "*",
            **kwargs: Any,
        ) -> list[dict[str, object]]:
            return [gap_learning]

        _patch_recall_deps(monkeypatch, mock_recall)

        config = _make_config()
        reader = FileStateReader()

        learnings, _, _ = perform_session_recalls(
            trw_dir=tmp_path,
            query="build adapter registry",
            config=config,
            reader=reader,
        )

        alert_entries = [
            e for e in learnings if "ANTI-PATTERN ALERT" in str(e.get("summary", ""))
        ]
        assert len(alert_entries) >= 1

    def test_integration_gap_keyword_with_framework_query(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.tools._ceremony_helpers import perform_session_recalls

        gap_learning = _make_learning("L004", "integration gap in service layer")

        def mock_recall(
            trw_dir: Path,
            query: str = "*",
            **kwargs: Any,
        ) -> list[dict[str, object]]:
            return [gap_learning]

        _patch_recall_deps(monkeypatch, mock_recall)

        config = _make_config()
        reader = FileStateReader()

        learnings, _, _ = perform_session_recalls(
            trw_dir=tmp_path,
            query="framework plugin system",
            config=config,
            reader=reader,
        )

        alert_entries = [
            e for e in learnings if "ANTI-PATTERN ALERT" in str(e.get("summary", ""))
        ]
        assert len(alert_entries) >= 1


# ---------------------------------------------------------------------------
# Tests: anti-pattern alert skipped for unrelated tasks
# ---------------------------------------------------------------------------


class TestAntipatternAlertSkipped:
    """When query does NOT suggest model/system work, no alert is added."""

    def test_fix_typo_query_no_alert(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.tools._ceremony_helpers import perform_session_recalls

        facade_learning = _make_learning("L001", "facade pattern found in adapter layer")

        def mock_recall(
            trw_dir: Path,
            query: str = "*",
            **kwargs: Any,
        ) -> list[dict[str, object]]:
            return [facade_learning]

        _patch_recall_deps(monkeypatch, mock_recall)

        config = _make_config()
        reader = FileStateReader()

        learnings, _, _ = perform_session_recalls(
            trw_dir=tmp_path,
            query="fix typo in readme",
            config=config,
            reader=reader,
        )

        alert_entries = [
            e for e in learnings if "ANTI-PATTERN ALERT" in str(e.get("summary", ""))
        ]
        assert len(alert_entries) == 0

    def test_wildcard_query_no_alert(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.tools._ceremony_helpers import perform_session_recalls

        facade_learning = _make_learning("L001", "facade pattern found in adapter layer")

        def mock_recall(
            trw_dir: Path,
            query: str = "*",
            **kwargs: Any,
        ) -> list[dict[str, object]]:
            return [facade_learning]

        _patch_recall_deps(monkeypatch, mock_recall)

        config = _make_config()
        reader = FileStateReader()

        learnings, _, _ = perform_session_recalls(
            trw_dir=tmp_path,
            query="*",
            config=config,
            reader=reader,
        )

        # Wildcard queries are NOT focused, so anti-pattern check skips
        alert_entries = [
            e for e in learnings if "ANTI-PATTERN ALERT" in str(e.get("summary", ""))
        ]
        assert len(alert_entries) == 0


# ---------------------------------------------------------------------------
# Tests: fail-open behavior
# ---------------------------------------------------------------------------


class TestAntipatternAlertFailOpen:
    """Anti-pattern alert must never raise -- fail-open returns unmodified results."""

    def test_none_summary_does_not_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.tools._ceremony_helpers import perform_session_recalls

        # Learning with None summary -- should not crash the alert scanner
        bad_learning: dict[str, object] = {"id": "L-BAD", "summary": None, "impact": 0.5}

        def mock_recall(
            trw_dir: Path,
            query: str = "*",
            **kwargs: Any,
        ) -> list[dict[str, object]]:
            return [bad_learning]

        _patch_recall_deps(monkeypatch, mock_recall)

        config = _make_config()
        reader = FileStateReader()

        # Must not raise
        learnings, _, _ = perform_session_recalls(
            trw_dir=tmp_path,
            query="model system adapter",
            config=config,
            reader=reader,
        )
        assert isinstance(learnings, list)

    def test_missing_id_field_does_not_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.tools._ceremony_helpers import perform_session_recalls

        bad_learning: dict[str, object] = {"summary": "facade pattern", "impact": 0.5}

        def mock_recall(
            trw_dir: Path,
            query: str = "*",
            **kwargs: Any,
        ) -> list[dict[str, object]]:
            return [bad_learning]

        _patch_recall_deps(monkeypatch, mock_recall)

        config = _make_config()
        reader = FileStateReader()

        # Must not raise
        learnings, _, _ = perform_session_recalls(
            trw_dir=tmp_path,
            query="model system",
            config=config,
            reader=reader,
        )
        assert isinstance(learnings, list)
