"""Tests for PRD-CORE-110, CORE-111, and CORE-116 audit gap fixes.

Fix A: MemoryEntry empty-string coercion (tested in trw-memory tests)
Fix B: phase_origin WARNING when no active run
Fix C: HYPOTHESIS in LearningConfidence enum
Fix D: anchors + anchor_validity on LearningEntry
Fix E: anchors passed through _save_yaml_backup
Fix F: compute_anchor_validity called at learn time
Fix G: _sanitize_path rejects traversal paths entirely
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.learning import LearningConfidence, LearningEntry
from trw_mcp.scoring._recall import _sanitize_path, infer_domains
from trw_mcp.tools._learning_helpers import LearningParams

# ---------------------------------------------------------------------------
# Fix C: HYPOTHESIS in LearningConfidence
# ---------------------------------------------------------------------------


class TestHypothesisConfidence:
    """HYPOTHESIS is the first member of LearningConfidence."""

    def test_hypothesis_is_first_member(self) -> None:
        """HYPOTHESIS should be the first enum value (before UNVERIFIED)."""
        members = list(LearningConfidence)
        assert members[0] == LearningConfidence.HYPOTHESIS

    def test_hypothesis_value(self) -> None:
        assert LearningConfidence.HYPOTHESIS.value == "hypothesis"

    def test_learning_entry_accepts_hypothesis(self) -> None:
        entry = LearningEntry(id="L-1", summary="s", detail="d", confidence="hypothesis")
        assert entry.confidence == "hypothesis"

    def test_hypothesis_lifecycle_ordering(self) -> None:
        """The lifecycle is hypothesis -> unverified -> ... -> verified."""
        members = list(LearningConfidence)
        names = [m.value for m in members]
        assert names.index("hypothesis") < names.index("unverified")
        assert names.index("unverified") < names.index("verified")


# ---------------------------------------------------------------------------
# Fix D: anchors + anchor_validity on LearningEntry
# ---------------------------------------------------------------------------


class TestLearningEntryAnchors:
    """LearningEntry has anchors and anchor_validity fields."""

    def test_default_anchors_empty_list(self) -> None:
        entry = LearningEntry(id="L-1", summary="s", detail="d")
        assert entry.anchors == []

    def test_default_anchor_validity_is_1(self) -> None:
        entry = LearningEntry(id="L-1", summary="s", detail="d")
        assert entry.anchor_validity == 1.0

    def test_custom_anchors_accepted(self) -> None:
        anchors = [{"file": "src/foo.py", "symbol_name": "bar", "symbol_type": "function"}]
        entry = LearningEntry(id="L-1", summary="s", detail="d", anchors=anchors)
        assert len(entry.anchors) == 1
        assert entry.anchors[0]["file"] == "src/foo.py"

    def test_custom_anchor_validity_accepted(self) -> None:
        entry = LearningEntry(id="L-1", summary="s", detail="d", anchor_validity=0.75)
        assert entry.anchor_validity == 0.75

    def test_anchor_validity_bounds(self) -> None:
        """anchor_validity is bounded [0.0, 1.0]."""
        with pytest.raises(Exception):
            LearningEntry(id="L-1", summary="s", detail="d", anchor_validity=1.5)
        with pytest.raises(Exception):
            LearningEntry(id="L-1", summary="s", detail="d", anchor_validity=-0.1)


# ---------------------------------------------------------------------------
# Fix E: LearningParams has anchors + anchor_validity
# ---------------------------------------------------------------------------


class TestLearningParamsAnchors:
    """LearningParams includes anchors and anchor_validity."""

    def test_default_anchors_none(self) -> None:
        params = LearningParams(
            summary="s",
            detail="d",
            learning_id="L-1",
            tags=[],
            evidence=[],
            impact=0.5,
            source_type="agent",
            source_identity="",
        )
        assert params.anchors is None

    def test_default_anchor_validity(self) -> None:
        params = LearningParams(
            summary="s",
            detail="d",
            learning_id="L-1",
            tags=[],
            evidence=[],
            impact=0.5,
            source_type="agent",
            source_identity="",
        )
        assert params.anchor_validity == 1.0

    def test_custom_anchors(self) -> None:
        anchors = [{"file": "src/foo.py", "symbol_name": "bar"}]
        params = LearningParams(
            summary="s",
            detail="d",
            learning_id="L-1",
            tags=[],
            evidence=[],
            impact=0.5,
            source_type="agent",
            source_identity="",
            anchors=anchors,
            anchor_validity=0.8,
        )
        assert params.anchors == anchors
        assert params.anchor_validity == 0.8


# ---------------------------------------------------------------------------
# Fix B: phase_origin WARNING log when no active run
# ---------------------------------------------------------------------------


class TestPhaseOriginWarning:
    """execute_learn logs WARNING when phase_origin detection finds no active run."""

    def test_phase_origin_warning_when_no_active_run(self) -> None:
        """When detect_current_phase returns None, a warning is logged."""
        from trw_mcp.tools._learn_impl import execute_learn

        with (
            patch("trw_mcp.tools._learn_impl.logger") as mock_logger,
            patch("trw_mcp.state._paths.detect_current_phase", return_value=None),
        ):
            mock_store = MagicMock(return_value=None)
            mock_gen_id = MagicMock(return_value="L-test-123")
            mock_save = MagicMock(return_value="/tmp/test.yaml")
            mock_analytics = MagicMock()
            mock_list = MagicMock(return_value=[])
            mock_dedup = MagicMock(return_value=None)

            from pathlib import Path

            from trw_mcp.models.config import TRWConfig

            config = TRWConfig()
            trw_dir = Path("/tmp/fake-trw")

            try:
                execute_learn(
                    summary="test summary",
                    detail="test detail",
                    trw_dir=trw_dir,
                    config=config,
                    _adapter_store=mock_store,
                    _generate_learning_id=mock_gen_id,
                    _save_learning_entry=mock_save,
                    _update_analytics=mock_analytics,
                    _list_active_learnings=mock_list,
                    _check_and_handle_dedup=mock_dedup,
                )
            except Exception:
                pass  # We only care about the warning log

            # Verify the warning was logged
            mock_logger.warning.assert_any_call("phase_origin_no_active_run")


# ---------------------------------------------------------------------------
# Fix G: _sanitize_path rejects traversal entirely
# ---------------------------------------------------------------------------


class TestSanitizePathTraversal:
    """_sanitize_path rejects paths with '..' entirely."""

    def test_traversal_rejected_entirely(self) -> None:
        """Any path containing '..' in path components returns empty string."""
        assert _sanitize_path("../../etc/passwd") == ""

    def test_traversal_mid_path_rejected(self) -> None:
        """Traversal in the middle of a path is also rejected."""
        assert _sanitize_path("backend/../etc/passwd") == ""

    def test_normal_path_preserved(self) -> None:
        """Normal paths are preserved without modification."""
        assert _sanitize_path("backend/payments/handler.py") == "backend/payments/handler.py"

    def test_absolute_path_stripped(self) -> None:
        """Leading '/' is stripped from normal absolute paths."""
        assert _sanitize_path("/etc/passwd") == "etc/passwd"

    def test_null_byte_rejected(self) -> None:
        """Paths with null bytes are rejected."""
        assert _sanitize_path("foo\x00bar") == ""

    def test_empty_path(self) -> None:
        """Empty path returns empty string."""
        assert _sanitize_path("") == ""

    def test_dotdot_in_filename_not_rejected(self) -> None:
        """A filename like 'foo..bar' is not rejected (no path component '..')."""
        result = _sanitize_path("src/foo..bar.py")
        assert result == "src/foo..bar.py"


class TestInferDomainsTraversalRejection:
    """infer_domains fully rejects traversal paths (no 'etc' domain leakage)."""

    def test_traversal_produces_empty_domains(self) -> None:
        """Path traversal '../../etc/passwd' produces no domains at all."""
        result = infer_domains(file_paths=["../../etc/passwd"])
        assert result == set()

    def test_mixed_paths_only_safe_domains(self) -> None:
        """Safe paths produce domains; traversal paths are silently dropped."""
        result = infer_domains(
            file_paths=[
                "backend/payments/handler.py",
                "../../etc/passwd",
            ]
        )
        assert "etc" not in result
        assert "passwd" not in result
        assert "backend" in result or "payments" in result
