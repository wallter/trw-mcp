"""CORE268: legacy failure ages do not authorize implicit recall verification.

These five historical CORE086 scenarios deliberately lack stored result/date
observations. Explicit maintenance owns threshold evaluation; recall must leave
this input untouched and report unknown, regardless of a mocked verifier result.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from trw_memory.models.memory import AssertionType

from trw_mcp.models.config import TRWConfig


def _make_assertion_dict(
    type_: AssertionType = AssertionType.GREP_PRESENT,
    pattern: str = "def my_func",
    target: str = "**/*.py",
    first_failed_at: datetime | None = None,
) -> dict[str, Any]:
    """Create a minimal assertion dict matching Assertion model shape."""
    return {
        "type": type_,
        "pattern": pattern,
        "target": target,
        "last_result": None,
        "last_verified_at": None,
        "last_evidence": "",
        "first_failed_at": first_failed_at,
    }


def _make_learning(
    entry_id: str,
    assertions: list[dict[str, Any]],
) -> dict[str, object]:
    """Create a minimal learning dict for auto-stale tests."""
    return {
        "id": entry_id,
        "summary": "test learning",
        "detail": "test detail",
        "tags": ["test"],
        "impact": 0.8,
        "status": "active",
        "assertions": assertions,
    }


def _make_assertion_result(passed: bool | None, evidence: str = "") -> MagicMock:
    """Create a mock AssertionResult with model_dump support."""
    result = MagicMock()
    result.passed = passed
    result.evidence = evidence
    result.model_dump.return_value = {
        "type": "grep_present",
        "pattern": "def my_func",
        "target": "**/*.py",
        "passed": passed,
        "evidence": evidence,
    }
    return result


@pytest.fixture()
def config() -> TRWConfig:
    """Provide a TRWConfig with default assertion settings (30-day threshold)."""
    return TRWConfig()


@pytest.fixture()
def mock_rank_fn() -> MagicMock:
    """Provide a mock rank function that returns its first argument."""

    def _rank(entries: list[dict[str, object]], *args: Any, **kwargs: Any) -> list[dict[str, object]]:
        return entries

    return MagicMock(side_effect=_rank)


class TestOldFailureAgeRemainsUnknown:
    """An old failure-age field is not a dated verification observation."""

    @patch("trw_mcp.state._paths.resolve_trw_dir")
    @patch("trw_mcp.state.memory_adapter.get_backend")
    @patch("trw_memory.lifecycle.verification.verify_assertions")
    @patch("trw_mcp.state._paths.resolve_project_root")
    def test_old_failure_age_remains_unknown(
        self,
        mock_resolve_root: MagicMock,
        mock_verify: MagicMock,
        mock_get_backend: MagicMock,
        mock_resolve_trw: MagicMock,
        config: TRWConfig,
        mock_rank_fn: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Recall cannot infer stale from unverified failure-age metadata."""
        from trw_mcp.tools._recall_impl import _verify_assertions

        mock_resolve_root.return_value = tmp_path
        mock_resolve_trw.return_value = tmp_path / ".trw"
        mock_backend = MagicMock()
        mock_get_backend.return_value = mock_backend

        # Both assertions fail
        mock_verify.return_value = [
            _make_assertion_result(passed=False),
            _make_assertion_result(passed=False),
        ]

        # Both have first_failed_at > 30 days ago
        old_failure = datetime.now(timezone.utc) - timedelta(days=45)
        learnings = [
            _make_learning(
                "L-stale",
                [
                    _make_assertion_dict(first_failed_at=old_failure),
                    _make_assertion_dict(
                        type_=AssertionType.GLOB_EXISTS,
                        pattern="",
                        target="src/main.py",
                        first_failed_at=old_failure,
                    ),
                ],
            ),
        ]

        original = deepcopy(learnings)
        mock_verify.side_effect = AssertionError("recall must not verify repository assertions")
        mock_get_backend.side_effect = AssertionError("recall qualification must not access storage")
        result = _verify_assertions(learnings, ["test"], config, mock_rank_fn)

        assert learnings == original
        mock_verify.assert_not_called()
        mock_get_backend.assert_not_called()
        assert mock_backend.mock_calls == []
        mock_resolve_root.assert_not_called()
        mock_resolve_trw.assert_not_called()
        mock_rank_fn.assert_not_called()
        assert result[0]["verification_evidence"]["current_tree_verified"] is False

        assert result[0].get("verification_status") == "unknown"


class TestRecentFailureAgeRemainsUnknown:
    """When first_failed_at is recent (< 30 days), learning is NOT marked stale."""

    @patch("trw_mcp.state._paths.resolve_trw_dir")
    @patch("trw_mcp.state.memory_adapter.get_backend")
    @patch("trw_memory.lifecycle.verification.verify_assertions")
    @patch("trw_mcp.state._paths.resolve_project_root")
    def test_recent_failure_not_stale(
        self,
        mock_resolve_root: MagicMock,
        mock_verify: MagicMock,
        mock_get_backend: MagicMock,
        mock_resolve_trw: MagicMock,
        config: TRWConfig,
        mock_rank_fn: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Learning is NOT marked stale when failures are recent."""
        from trw_mcp.tools._recall_impl import _verify_assertions

        mock_resolve_root.return_value = tmp_path
        mock_resolve_trw.return_value = tmp_path / ".trw"
        mock_backend = MagicMock()
        mock_get_backend.return_value = mock_backend

        mock_verify.return_value = [
            _make_assertion_result(passed=False),
        ]

        # first_failed_at is only 5 days ago (under 30-day threshold)
        recent_failure = datetime.now(timezone.utc) - timedelta(days=5)
        learnings = [
            _make_learning(
                "L-recent",
                [
                    _make_assertion_dict(first_failed_at=recent_failure),
                ],
            ),
        ]

        original = deepcopy(learnings)
        mock_verify.side_effect = AssertionError("recall must not verify repository assertions")
        mock_get_backend.side_effect = AssertionError("recall qualification must not access storage")
        result = _verify_assertions(learnings, ["test"], config, mock_rank_fn)

        assert learnings == original
        mock_verify.assert_not_called()
        mock_get_backend.assert_not_called()
        assert mock_backend.mock_calls == []
        mock_resolve_root.assert_not_called()
        mock_resolve_trw.assert_not_called()
        mock_rank_fn.assert_not_called()
        assert result[0]["verification_evidence"]["current_tree_verified"] is False

        assert result[0].get("verification_status") == "unknown"


class TestUnobservedMixedResultsRemainUnknown:
    """When some assertions pass and some fail, NOT marked stale."""

    @patch("trw_mcp.state._paths.resolve_trw_dir")
    @patch("trw_mcp.state.memory_adapter.get_backend")
    @patch("trw_memory.lifecycle.verification.verify_assertions")
    @patch("trw_mcp.state._paths.resolve_project_root")
    def test_mixed_pass_fail_not_stale(
        self,
        mock_resolve_root: MagicMock,
        mock_verify: MagicMock,
        mock_get_backend: MagicMock,
        mock_resolve_trw: MagicMock,
        config: TRWConfig,
        mock_rank_fn: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Mixed pass/fail assertions do not trigger stale marking."""
        from trw_mcp.tools._recall_impl import _verify_assertions

        mock_resolve_root.return_value = tmp_path
        mock_resolve_trw.return_value = tmp_path / ".trw"
        mock_backend = MagicMock()
        mock_get_backend.return_value = mock_backend

        # One passes, one fails
        mock_verify.return_value = [
            _make_assertion_result(passed=True),
            _make_assertion_result(passed=False),
        ]

        old_failure = datetime.now(timezone.utc) - timedelta(days=45)
        learnings = [
            _make_learning(
                "L-mixed",
                [
                    _make_assertion_dict(first_failed_at=None),  # passes -> cleared
                    _make_assertion_dict(
                        type_=AssertionType.GLOB_EXISTS,
                        pattern="",
                        target="src/main.py",
                        first_failed_at=old_failure,
                    ),
                ],
            ),
        ]

        original = deepcopy(learnings)
        mock_verify.side_effect = AssertionError("recall must not verify repository assertions")
        mock_get_backend.side_effect = AssertionError("recall qualification must not access storage")
        result = _verify_assertions(learnings, ["test"], config, mock_rank_fn)

        assert learnings == original
        mock_verify.assert_not_called()
        mock_get_backend.assert_not_called()
        assert mock_backend.mock_calls == []
        mock_resolve_root.assert_not_called()
        mock_resolve_trw.assert_not_called()
        mock_rank_fn.assert_not_called()
        assert result[0]["verification_evidence"]["current_tree_verified"] is False

        # Mocked results are not observations until explicit maintenance runs.
        assert result[0].get("verification_status") == "unknown"


class TestMissingFailureAgeRemainsUnknown:
    """When first_failed_at is None (new failure), NOT marked stale."""

    @patch("trw_mcp.state._paths.resolve_trw_dir")
    @patch("trw_mcp.state.memory_adapter.get_backend")
    @patch("trw_memory.lifecycle.verification.verify_assertions")
    @patch("trw_mcp.state._paths.resolve_project_root")
    def test_no_first_failed_at_not_stale(
        self,
        mock_resolve_root: MagicMock,
        mock_verify: MagicMock,
        mock_get_backend: MagicMock,
        mock_resolve_trw: MagicMock,
        config: TRWConfig,
        mock_rank_fn: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Learning with first_failed_at=None is not marked stale even if failing."""
        from trw_mcp.tools._recall_impl import _verify_assertions

        mock_resolve_root.return_value = tmp_path
        mock_resolve_trw.return_value = tmp_path / ".trw"
        mock_backend = MagicMock()
        mock_get_backend.return_value = mock_backend

        mock_verify.return_value = [
            _make_assertion_result(passed=False),
        ]

        # first_failed_at is None — this is a brand new failure
        learnings = [
            _make_learning(
                "L-new-fail",
                [
                    _make_assertion_dict(first_failed_at=None),
                ],
            ),
        ]

        original = deepcopy(learnings)
        mock_verify.side_effect = AssertionError("recall must not verify repository assertions")
        mock_get_backend.side_effect = AssertionError("recall qualification must not access storage")
        result = _verify_assertions(learnings, ["test"], config, mock_rank_fn)

        assert learnings == original
        mock_verify.assert_not_called()
        mock_get_backend.assert_not_called()
        assert mock_backend.mock_calls == []
        mock_resolve_root.assert_not_called()
        mock_resolve_trw.assert_not_called()
        mock_rank_fn.assert_not_called()
        assert result[0]["verification_evidence"]["current_tree_verified"] is False

        # Recall must not set first_failed_at or infer a result from its absence.
        assert result[0].get("verification_status") == "unknown"


class TestCustomThresholdDoesNotAuthorizeRecallVerification:
    """The maintenance threshold cannot make absent recall evidence known."""

    @patch("trw_mcp.state._paths.resolve_trw_dir")
    @patch("trw_mcp.state.memory_adapter.get_backend")
    @patch("trw_memory.lifecycle.verification.verify_assertions")
    @patch("trw_mcp.state._paths.resolve_project_root")
    def test_custom_threshold_respected(
        self,
        mock_resolve_root: MagicMock,
        mock_verify: MagicMock,
        mock_get_backend: MagicMock,
        mock_resolve_trw: MagicMock,
        mock_rank_fn: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Even a short maintenance threshold leaves this recall evidence unknown."""
        from trw_mcp.tools._recall_impl import _verify_assertions

        # Use a very short threshold of 3 days
        config = TRWConfig(assertion_stale_threshold_days=3)

        mock_resolve_root.return_value = tmp_path
        mock_resolve_trw.return_value = tmp_path / ".trw"
        mock_backend = MagicMock()
        mock_get_backend.return_value = mock_backend

        mock_verify.return_value = [
            _make_assertion_result(passed=False),
        ]

        # first_failed_at is 5 days ago — under default 30 but over custom 3
        failure_time = datetime.now(timezone.utc) - timedelta(days=5)
        learnings = [
            _make_learning(
                "L-custom",
                [
                    _make_assertion_dict(first_failed_at=failure_time),
                ],
            ),
        ]

        original = deepcopy(learnings)
        mock_verify.side_effect = AssertionError("recall must not verify repository assertions")
        mock_get_backend.side_effect = AssertionError("recall qualification must not access storage")
        result = _verify_assertions(learnings, ["test"], config, mock_rank_fn)

        assert learnings == original
        mock_verify.assert_not_called()
        mock_get_backend.assert_not_called()
        assert mock_backend.mock_calls == []
        mock_resolve_root.assert_not_called()
        mock_resolve_trw.assert_not_called()
        mock_rank_fn.assert_not_called()
        assert result[0]["verification_evidence"]["current_tree_verified"] is False

        assert result[0].get("verification_status") == "unknown"
