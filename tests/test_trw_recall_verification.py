"""Tests for lazy assertion verification in trw_recall (PRD-CORE-086 FR06).

Verifies that _verify_assertions() attaches assertion_status, handles edge cases,
persists verification results, and manages first_failed_at transitions.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
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
    d: dict[str, Any] = {
        "type": type_,
        "pattern": pattern,
        "target": target,
        "last_result": None,
        "last_verified_at": None,
        "last_evidence": "",
        "first_failed_at": first_failed_at,
    }
    return d


def _make_learning(
    entry_id: str,
    assertions: list[dict[str, Any]] | None = None,
) -> dict[str, object]:
    """Create a minimal learning dict for verification tests."""
    d: dict[str, object] = {
        "id": entry_id,
        "summary": "test learning",
        "detail": "test detail",
        "tags": ["test"],
        "impact": 0.8,
        "status": "active",
    }
    if assertions is not None:
        d["assertions"] = assertions
    return d


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
    """Provide a TRWConfig instance with default assertion settings."""
    return TRWConfig()


@pytest.fixture()
def mock_rank_fn() -> MagicMock:
    """Provide a mock rank function that returns its first argument."""

    def _rank(entries: list[dict[str, object]], *args: Any, **kwargs: Any) -> list[dict[str, object]]:
        return entries

    return MagicMock(side_effect=_rank)


class TestVerifyAssertionsAttachesStatus:
    """_verify_assertions attaches assertion_status dict to learnings with assertions."""

    @patch("trw_mcp.state._paths.resolve_trw_dir")
    @patch("trw_mcp.state.memory_adapter.get_backend")
    @patch("trw_memory.lifecycle.verification.verify_assertions")
    @patch("trw_mcp.state._paths.resolve_project_root")
    def test_verify_assertions_attaches_status(
        self,
        mock_resolve_root: MagicMock,
        mock_verify: MagicMock,
        mock_get_backend: MagicMock,
        mock_resolve_trw: MagicMock,
        config: TRWConfig,
        mock_rank_fn: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Learnings with assertions get assertion_status with passing/failing/stale counts."""
        from trw_mcp.tools._recall_impl import _verify_assertions

        mock_resolve_root.return_value = tmp_path
        mock_resolve_trw.return_value = tmp_path / ".trw"
        mock_backend = MagicMock()
        mock_get_backend.return_value = mock_backend

        # Two assertions: one passes, one fails
        mock_verify.return_value = [
            _make_assertion_result(passed=True, evidence="found"),
            _make_assertion_result(passed=False, evidence="not found"),
        ]

        learnings = [
            _make_learning("L-1", assertions=[
                _make_assertion_dict(),
                _make_assertion_dict(type_=AssertionType.GLOB_EXISTS, pattern="", target="src/main.py"),
            ]),
        ]

        result = _verify_assertions(learnings, ["test"], config, mock_rank_fn)

        assert len(result) == 1
        assert "assertion_status" in result[0]
        status = result[0]["assertion_status"]
        assert isinstance(status, dict)
        assert status["passing"] == 1
        assert status["failing"] == 1
        assert status["stale"] == 0
        assert "details" in status
        assert status["details"][0]["id"] == "L-1:1"
        assert status["details"][1]["id"] == "L-1:2"


class TestVerifyAssertionsSkipsNoAssertions:
    """Learnings without assertions are returned unchanged."""

    @patch("trw_mcp.state._paths.resolve_project_root")
    def test_verify_assertions_skips_no_assertions(
        self,
        mock_resolve_root: MagicMock,
        config: TRWConfig,
        mock_rank_fn: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Learnings without assertions are returned without assertion_status."""
        from trw_mcp.tools._recall_impl import _verify_assertions

        mock_resolve_root.return_value = tmp_path

        learnings = [_make_learning("L-1")]  # No assertions field
        result = _verify_assertions(learnings, ["test"], config, mock_rank_fn)

        assert len(result) == 1
        assert "assertion_status" not in result[0]


class TestVerifyAssertionsNoProjectRoot:
    """When project_root cannot be resolved, learnings still get stale verification status."""

    @patch("trw_mcp.state._paths.resolve_trw_dir")
    @patch("trw_mcp.state.memory_adapter.get_backend")
    @patch("trw_memory.lifecycle.verification.verify_assertions")
    @patch("trw_mcp.state._paths.resolve_project_root")
    def test_verify_assertions_no_project_root(
        self,
        mock_resolve_root: MagicMock,
        mock_verify: MagicMock,
        mock_get_backend: MagicMock,
        mock_resolve_trw: MagicMock,
        config: TRWConfig,
        mock_rank_fn: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When resolve_project_root returns None, assertion_status still reflects unverifiable assertions."""
        from trw_mcp.tools._recall_impl import _verify_assertions

        mock_resolve_root.return_value = None
        mock_resolve_trw.return_value = tmp_path / ".trw"
        mock_backend = MagicMock()
        mock_get_backend.return_value = mock_backend
        mock_verify.return_value = [
            _make_assertion_result(passed=None, evidence="project_root unavailable"),
        ]

        learnings = [
            _make_learning("L-1", assertions=[_make_assertion_dict()]),
        ]
        result = _verify_assertions(learnings, ["test"], config, mock_rank_fn)

        assert len(result) == 1
        status = result[0]["assertion_status"]
        assert status["passing"] == 0
        assert status["failing"] == 0
        assert status["stale"] == 1
        assert status["details"][0]["id"] == "L-1:1"
        assert status["details"][0]["passed"] is None
        assert status["details"][0]["evidence"] == "project_root unavailable"


class TestVerifyAssertionsPersistsResults:
    """After verification, backend.update() is called with updated assertion JSON."""

    @patch("trw_mcp.state._paths.resolve_trw_dir")
    @patch("trw_mcp.state.memory_adapter.get_backend")
    @patch("trw_memory.lifecycle.verification.verify_assertions")
    @patch("trw_mcp.state._paths.resolve_project_root")
    def test_verify_assertions_persists_results(
        self,
        mock_resolve_root: MagicMock,
        mock_verify: MagicMock,
        mock_get_backend: MagicMock,
        mock_resolve_trw: MagicMock,
        config: TRWConfig,
        mock_rank_fn: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Backend.update() is called with updated assertions JSON after verification."""
        from trw_mcp.tools._recall_impl import _verify_assertions

        mock_resolve_root.return_value = tmp_path
        mock_resolve_trw.return_value = tmp_path / ".trw"
        mock_backend = MagicMock()
        mock_get_backend.return_value = mock_backend

        mock_verify.return_value = [
            _make_assertion_result(passed=True, evidence="found in code"),
        ]

        learnings = [
            _make_learning("L-persist", assertions=[_make_assertion_dict()]),
        ]

        _verify_assertions(learnings, ["test"], config, mock_rank_fn)

        mock_backend.update.assert_called_once()
        call_args = mock_backend.update.call_args
        assert call_args[0][0] == "L-persist"  # entry_id
        # The assertions kwarg should be a JSON string
        assertions_json = call_args[1]["assertions"]
        parsed = json.loads(assertions_json)
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert parsed[0]["last_result"] is True


class TestFirstFailedAtSetOnFailure:
    """When an assertion fails and first_failed_at was None, it gets set."""

    @patch("trw_mcp.state._paths.resolve_trw_dir")
    @patch("trw_mcp.state.memory_adapter.get_backend")
    @patch("trw_memory.lifecycle.verification.verify_assertions")
    @patch("trw_mcp.state._paths.resolve_project_root")
    def test_first_failed_at_set_on_failure(
        self,
        mock_resolve_root: MagicMock,
        mock_verify: MagicMock,
        mock_get_backend: MagicMock,
        mock_resolve_trw: MagicMock,
        config: TRWConfig,
        mock_rank_fn: MagicMock,
        tmp_path: Path,
    ) -> None:
        """first_failed_at is set to now when an assertion fails and was previously None."""
        from trw_mcp.tools._recall_impl import _verify_assertions

        mock_resolve_root.return_value = tmp_path
        mock_resolve_trw.return_value = tmp_path / ".trw"
        mock_backend = MagicMock()
        mock_get_backend.return_value = mock_backend

        # Assertion fails
        mock_verify.return_value = [
            _make_assertion_result(passed=False, evidence="not found"),
        ]

        # first_failed_at starts as None
        learnings = [
            _make_learning("L-fail", assertions=[
                _make_assertion_dict(first_failed_at=None),
            ]),
        ]

        _verify_assertions(learnings, ["test"], config, mock_rank_fn)

        # Check persisted assertions have first_failed_at set
        call_args = mock_backend.update.call_args
        assertions_json = call_args[1]["assertions"]
        parsed = json.loads(assertions_json)
        assert parsed[0]["first_failed_at"] is not None
        # Verify it's a valid ISO timestamp
        datetime.fromisoformat(parsed[0]["first_failed_at"])


class TestFirstFailedAtClearedOnPass:
    """When an assertion passes after failure, first_failed_at is cleared."""

    @patch("trw_mcp.state._paths.resolve_trw_dir")
    @patch("trw_mcp.state.memory_adapter.get_backend")
    @patch("trw_memory.lifecycle.verification.verify_assertions")
    @patch("trw_mcp.state._paths.resolve_project_root")
    def test_first_failed_at_cleared_on_pass(
        self,
        mock_resolve_root: MagicMock,
        mock_verify: MagicMock,
        mock_get_backend: MagicMock,
        mock_resolve_trw: MagicMock,
        config: TRWConfig,
        mock_rank_fn: MagicMock,
        tmp_path: Path,
    ) -> None:
        """first_failed_at is cleared (None) when assertion transitions to passing."""
        from trw_mcp.tools._recall_impl import _verify_assertions

        mock_resolve_root.return_value = tmp_path
        mock_resolve_trw.return_value = tmp_path / ".trw"
        mock_backend = MagicMock()
        mock_get_backend.return_value = mock_backend

        # Assertion now passes
        mock_verify.return_value = [
            _make_assertion_result(passed=True, evidence="found again"),
        ]

        # first_failed_at was previously set
        past = datetime(2026, 1, 1, tzinfo=timezone.utc)
        learnings = [
            _make_learning("L-recover", assertions=[
                _make_assertion_dict(first_failed_at=past),
            ]),
        ]

        _verify_assertions(learnings, ["test"], config, mock_rank_fn)

        call_args = mock_backend.update.call_args
        assertions_json = call_args[1]["assertions"]
        parsed = json.loads(assertions_json)
        assert parsed[0]["first_failed_at"] is None
