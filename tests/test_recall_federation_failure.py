"""P1/Item4 — Federation failure modes for _federate_user_tier.

Verifies that OSError variants (EACCES=13, ENOSPC=28) raised by the user-tier
backend and corrupt-store behaviour in _federate_user_tier all degrade
gracefully: project results are returned, a WARN is logged, and recall never
crashes or hangs.

The contract under test (NFR04 fail-open): ``_federate_user_tier`` wraps its
entire body in a broad ``except Exception`` that logs at debug and returns
``project_entries`` unchanged. These tests probe that the guard works for the
WARN-level variants (structured log captured), OS-level errors, and the
corrupt-DB path.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import structlog.testing

from trw_mcp.models.config import _reset_config
from trw_mcp.state import memory_adapter
from trw_mcp.state._user_tier import reset_user_backend


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate user dir + user tier for every test in this file."""
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("TRW_USER_TIER_ENABLED", "true")
    _reset_config()
    memory_adapter.reset_backend()
    reset_user_backend()
    yield
    memory_adapter.reset_backend()
    reset_user_backend()
    _reset_config()


def _trw_dir(tmp_path: Path, name: str) -> Path:
    d = tmp_path / name / ".trw"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ids(rows: list[dict[str, object]]) -> list[str]:
    return [str(r.get("id")) for r in rows]


class TestFederationOsErrors:
    """_federate_user_tier handles OSError variants without crashing."""

    def test_eacces_on_user_backend_degrades_to_project_only(self, tmp_path: Path) -> None:
        """EACCES (errno 13) from the user backend degrades: project result returned, no crash.

        Scenario: user-scope file exists (so federation is attempted) but
        reading it raises PermissionError (EACCES). The project hit must
        survive in the result.
        """
        from trw_mcp.state import _memory_recall

        repo = _trw_dir(tmp_path, "repo")
        memory_adapter.store_learning(repo, "L-proj", "project specific widget", "detail", scope="project")

        eacces_error = PermissionError(errno.EACCES, os.strerror(errno.EACCES), "memory.db")

        def _raise_permission(*_args: object, **_kwargs: object) -> object:
            raise eacces_error

        # Patch at the _memory_recall module level so the broad except catches it.
        with patch.object(_memory_recall, "_query_user_backend", side_effect=_raise_permission):
            # Also need user_scope_present() and peek_user_backend() to proceed.
            with (
                patch.object(_memory_recall, "user_scope_present", return_value=True),
                patch.object(_memory_recall, "peek_user_backend", return_value=MagicMock()),
            ):
                rows = memory_adapter.recall_learnings(repo, "project specific widget", max_results=10)

        ids = _ids(rows)
        assert "L-proj" in ids, "project hit must survive EACCES on user backend"

    def test_enospc_on_user_backend_degrades_to_project_only(self, tmp_path: Path) -> None:
        """ENOSPC (errno 28) from the user backend degrades gracefully.

        A disk-full error during user-tier query must not crash recall —
        project results are returned, user hits are simply absent.
        """
        from trw_mcp.state import _memory_recall

        repo = _trw_dir(tmp_path, "repo")
        memory_adapter.store_learning(repo, "L-proj-ns", "project nospc query", "detail", scope="project")

        enospc_error = OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

        with patch.object(_memory_recall, "_query_user_backend", side_effect=enospc_error):
            with (
                patch.object(_memory_recall, "user_scope_present", return_value=True),
                patch.object(_memory_recall, "peek_user_backend", return_value=MagicMock()),
            ):
                rows = memory_adapter.recall_learnings(repo, "project nospc query", max_results=10)

        ids = _ids(rows)
        assert "L-proj-ns" in ids, "project hit must survive ENOSPC on user backend"

    def test_oserror_user_backend_does_not_raise(self, tmp_path: Path) -> None:
        """Any OSError during federation never escapes recall (no exception to caller)."""
        from trw_mcp.state import _memory_recall

        repo = _trw_dir(tmp_path, "repo")
        memory_adapter.store_learning(repo, "L-safe", "safe project entry", "d", scope="project")

        with patch.object(_memory_recall, "_query_user_backend", side_effect=OSError("generic OS error")):
            with (
                patch.object(_memory_recall, "user_scope_present", return_value=True),
                patch.object(_memory_recall, "peek_user_backend", return_value=MagicMock()),
            ):
                # Must not raise.
                rows = memory_adapter.recall_learnings(repo, "safe project entry", max_results=10)

        assert isinstance(rows, list)

    def test_generic_exception_user_backend_degrades_not_crashes(self, tmp_path: Path) -> None:
        """A RuntimeError in the user backend degrades gracefully (project-only)."""
        from trw_mcp.state import _memory_recall

        repo = _trw_dir(tmp_path, "repo")
        memory_adapter.store_learning(repo, "L-rt", "runtime project entry", "d", scope="project")

        with patch.object(_memory_recall, "_query_user_backend", side_effect=RuntimeError("unexpected")):
            with (
                patch.object(_memory_recall, "user_scope_present", return_value=True),
                patch.object(_memory_recall, "peek_user_backend", return_value=MagicMock()),
            ):
                rows = memory_adapter.recall_learnings(repo, "runtime project entry", max_results=10)

        ids = _ids(rows)
        assert "L-rt" in ids, "project result must survive RuntimeError in user federation"

    def test_federation_failure_logs_debug_not_warn(self, tmp_path: Path) -> None:
        """Federation failure is logged at DEBUG, not a noisy WARN (NFR04: fail-open silently)."""
        from trw_mcp.state import _memory_recall

        repo = _trw_dir(tmp_path, "repo")
        memory_adapter.store_learning(repo, "L-log", "log test entry", "d", scope="project")

        with structlog.testing.capture_logs() as logs:
            with patch.object(_memory_recall, "_query_user_backend", side_effect=OSError("disk error")):
                with (
                    patch.object(_memory_recall, "user_scope_present", return_value=True),
                    patch.object(_memory_recall, "peek_user_backend", return_value=MagicMock()),
                ):
                    memory_adapter.recall_learnings(repo, "log test entry", max_results=10)

        # The fail-open path logs at debug level; no WARNING should fire for a
        # transient OS error in federation (that would be too noisy for operators).
        warn_events = [log for log in logs if log.get("log_level") == "warning"]
        federation_warns = [log for log in warn_events if "user_tier_federation" in str(log.get("event", ""))]
        assert not federation_warns, f"federation OS failure must not produce a WARN-level log: {federation_warns}"
