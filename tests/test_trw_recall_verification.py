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


def _persisted_assertions(call_args: Any) -> list[dict[str, Any]]:
    """Return the assertion payload handed to ``backend.update(namespace="default")`` as plain dicts."""
    written = call_args[1]["assertions"]
    assert isinstance(written, list)
    return [json.loads(a.model_dump_json()) for a in written]


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
            _make_learning(
                "L-1",
                assertions=[
                    _make_assertion_dict(),
                    _make_assertion_dict(type_=AssertionType.GLOB_EXISTS, pattern="", target="src/main.py"),
                ],
            ),
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
    """After verification, backend.update(namespace="default") is called with updated assertion JSON."""

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
        """Backend.update(namespace="default") is called with updated assertions JSON after verification."""
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
        # PRD-CORE-231-FR02: the kwarg carries validated Assertion MODELS, not a
        # pre-serialized JSON string — update() reconstructs the entry to hash it
        # and a raw string there makes that reconstruction raise.
        parsed = _persisted_assertions(call_args)
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
            _make_learning(
                "L-fail",
                assertions=[
                    _make_assertion_dict(first_failed_at=None),
                ],
            ),
        ]

        _verify_assertions(learnings, ["test"], config, mock_rank_fn)

        # Check persisted assertions have first_failed_at set
        parsed = _persisted_assertions(mock_backend.update.call_args)
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
            _make_learning(
                "L-recover",
                assertions=[
                    _make_assertion_dict(first_failed_at=past),
                ],
            ),
        ]

        _verify_assertions(learnings, ["test"], config, mock_rank_fn)

        parsed = _persisted_assertions(mock_backend.update.call_args)
        assert parsed[0]["first_failed_at"] is None


# ---------------------------------------------------------------------------
# PRD-CORE-244 FR03 — a verification verdict gains a positive value + a stamp.
#
# These run against a REAL SQLiteBackend and the REAL assertion/anchor
# verification: the whole point of the FR is that the verdict survives storage,
# and a mocked backend cannot show that.
# ---------------------------------------------------------------------------


def _real_store(tmp_path: Path, entry_id: str, assertions: list[dict[str, Any]], anchors: list[dict[str, str]]) -> Any:
    """Create a real backend holding one entry with *assertions* and *anchors*."""
    from trw_memory.models.memory import Anchor, Assertion, MemoryEntry
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    backend = SQLiteBackend(tmp_path / ".trw" / "memory.db")
    backend.store(
        MemoryEntry(
            id=entry_id,
            content="verified verdict round trip",
            namespace="default",
            assertions=[Assertion.model_validate(a, strict=False) for a in assertions],
            anchors=[Anchor.model_validate(a, strict=True) for a in anchors],
        )
    )
    return backend


def _source_tree(tmp_path: Path, body: str = "def my_func():\n    return 1\n") -> None:
    """Write the source file the grep_present assertion + anchors resolve against."""
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "main.py").write_text(body, encoding="utf-8")


def _run_pass(tmp_path: Path, backend: Any, learning: dict[str, object], config: TRWConfig) -> None:
    from trw_mcp.tools._recall_impl import _verify_assertions

    def _rank(entries: list[dict[str, object]], *args: Any, **kwargs: Any) -> list[dict[str, object]]:
        return entries

    with (
        patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
        patch("trw_mcp.state._paths.resolve_trw_dir", return_value=tmp_path / ".trw"),
        patch("trw_mcp.state.memory_adapter.get_backend", return_value=backend),
    ):
        _verify_assertions([learning], ["verified"], config, MagicMock(side_effect=_rank))


@pytest.mark.unit
def test_positive_verdict_and_checked_at_persisted(tmp_path: Path, config: TRWConfig) -> None:
    """FR03: a clean pass persists ``verified`` AND the timestamp that proves it ran.

    Before FR03 the vocabulary had no positive value at all, so this entry came
    back with ``verification_status=None`` — indistinguishable from an entry no
    pass had ever looked at.
    """
    _source_tree(tmp_path)
    assertion = _make_assertion_dict(target="src/*.py")
    backend = _real_store(tmp_path, "L-verified", [assertion], [])

    learning = _make_learning("L-verified", assertions=[assertion])
    learning["namespace"] = "default"
    _run_pass(tmp_path, backend, learning, config)

    assert learning["verification_status"] == "verified"

    stored = backend.get("L-verified", namespace="default")
    assert stored is not None
    assert stored.verification_status == "verified"
    assert stored.verification_checked_at != ""
    datetime.fromisoformat(stored.verification_checked_at)  # a real ISO-8601 stamp


@pytest.mark.unit
def test_anchor_drift_below_floor_withholds_verified(tmp_path: Path, config: TRWConfig) -> None:
    """FR03: a drifted anchor set is not a clean bill of health.

    Half the anchors resolve, so the recomputed score lands under
    ``anchor_validity_verified_floor`` and the positive verdict is withheld —
    without inventing a ``stale`` conviction the staleness rule never reached.
    """
    _source_tree(tmp_path)
    assertion = _make_assertion_dict(target="src/*.py")
    anchors = [
        {"file": "src/main.py", "symbol_name": "my_func"},
        {"file": "src/main.py", "symbol_name": "deleted_func"},
    ]
    backend = _real_store(tmp_path, "L-drift", [assertion], anchors)

    learning = _make_learning("L-drift", assertions=[assertion])
    learning["namespace"] = "default"
    learning["anchors"] = anchors
    _run_pass(tmp_path, backend, learning, config)

    stored = backend.get("L-drift", namespace="default")
    assert stored is not None
    assert stored.anchor_validity is not None
    assert stored.anchor_validity < config.anchor_validity_verified_floor
    assert stored.verification_status is None
    # It WAS examined — the stamp is what distinguishes this from "never checked".
    assert stored.verification_checked_at != ""
    assert "verification_status" not in learning


@pytest.mark.unit
def test_warm_verdict_is_reused_within_ttl(tmp_path: Path, config: TRWConfig) -> None:
    """FR03: inside the TTL the persisted verdict is reused and nothing is re-checked.

    The assertion here would FAIL if it ran (the symbol is gone), so a reused
    ``verified`` verdict and an untouched ``last_verified_at`` together prove no
    filesystem verification happened for this entry.
    """
    _source_tree(tmp_path, body="def something_else():\n    return 1\n")
    assertion = _make_assertion_dict(target="src/*.py")
    backend = _real_store(tmp_path, "L-warm", [assertion], [])
    backend.update(
        "L-warm",
        namespace="default",
        verification_status="verified",
        verification_checked_at=datetime.now(timezone.utc).isoformat(),
    )

    learning = _make_learning("L-warm", assertions=[assertion])
    learning["namespace"] = "default"
    _run_pass(tmp_path, backend, learning, config)

    assert learning["verification_status"] == "verified"
    stored = backend.get("L-warm", namespace="default")
    assert stored is not None
    assert stored.verification_status == "verified"
    assert stored.assertions[0].last_verified_at is None  # the pass never ran


@pytest.mark.unit
def test_expired_verdict_is_re_verified(tmp_path: Path, config: TRWConfig) -> None:
    """The falsification of the cache: outside the TTL the pass runs again."""
    from datetime import timedelta

    _source_tree(tmp_path, body="def something_else():\n    return 1\n")
    assertion = _make_assertion_dict(target="src/*.py")
    backend = _real_store(tmp_path, "L-cold", [assertion], [])
    stale_stamp = datetime.now(timezone.utc) - timedelta(seconds=config.verification_cache_ttl_seconds + 60)
    backend.update(
        "L-cold",
        namespace="default",
        verification_status="verified",
        verification_checked_at=stale_stamp.isoformat(),
    )

    learning = _make_learning("L-cold", assertions=[assertion])
    learning["namespace"] = "default"
    _run_pass(tmp_path, backend, learning, config)

    stored = backend.get("L-cold", namespace="default")
    assert stored is not None
    assert stored.assertions[0].last_verified_at is not None  # it really re-ran
    assert stored.verification_status is None  # the failing assertion withdrew it
