"""Tests for PRD-FIX-053-FR01: the embedding health advisory.

check_embeddings_status() returns an advisory when embeddings are enabled but unavailable
(update-project's warning). Session start no longer calls it: the daemon owns the
model, and session start reports the daemon-measured coverage instead
(tests/test_session_start_embedding_coverage.py).
FR07's embed-failure counter counted trw-mcp's own store-time embeds; the daemon
embeds on store now, so the counter went with that path.
"""

from __future__ import annotations

import pytest


class TestEmbedHealthAdvisory:
    """FR01: check_embeddings_status returns advisory when embeddings unavailable."""

    def test_advisory_when_enabled_but_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """embeddings_enabled=True but no embedder -> advisory naming the install.

        This used to patch ``_memory_connection.check_embeddings_status`` itself
        with a lambda returning a hardcoded dict, then assert that its own
        lambda's strings came back. The advisory logic FR01 exists to pin was
        therefore never executed: the test passed with the real implementation
        deleted. It now drives ``build_embeddings_status``, which takes its
        collaborators as parameters, so the real branch runs against an absent
        embedder with nothing about the outcome supplied by the test.
        """
        from trw_mcp.models.config import get_config
        from trw_mcp.state._memory_embedding_status import build_embeddings_status

        monkeypatch.setattr(get_config(), "embeddings_enabled", True, raising=False)

        result = build_embeddings_status(
            embedder_unavailable_reason="sentence-transformers is not installed",
            get_embedder=lambda: None,
            append_wal_health=lambda _result: None,
        )

        assert result["enabled"] is True
        assert result["available"] is False
        advisory = str(result.get("advisory", ""))
        assert advisory, "an enabled-but-unavailable embedder must produce an advisory"
        assert "pip install" in advisory or "trw-memory" in advisory, (
            f"Advisory must include install instructions, got: {advisory!r}"
        )

    @staticmethod
    def _status(monkeypatch: pytest.MonkeyPatch, *, enabled: bool, embedder: object) -> dict[str, object]:
        from trw_mcp.models.config import get_config
        from trw_mcp.state._memory_embedding_status import build_embeddings_status

        monkeypatch.setattr(get_config(), "embeddings_enabled", enabled, raising=False)
        return build_embeddings_status(
            embedder_unavailable_reason="",
            get_embedder=lambda: embedder,
            append_wal_health=lambda _result: None,
        )

    def test_no_advisory_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """embeddings working normally → available=True, empty advisory."""
        result = self._status(monkeypatch, enabled=True, embedder=object())

        assert (result["enabled"], result["available"], result["advisory"]) == (True, True, "")

    def test_disabled_embeddings_no_advisory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """embeddings_enabled=False → enabled=False, no advisory, and no embedder is asked for."""
        result = self._status(monkeypatch, enabled=False, embedder=None)

        assert (result["enabled"], result["advisory"]) == (False, "")

    def test_advisory_reports_runtime_failure_reason(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Installed-but-broken embeddings should not be reported as merely missing."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state import _memory_connection

        mock_config = TRWConfig.__new__(TRWConfig)
        object.__setattr__(mock_config, "embeddings_enabled", True)
        monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: mock_config)
        monkeypatch.setattr(_memory_connection, "get_embedder", lambda: None)
        monkeypatch.setattr(
            _memory_connection,
            "_embedder_unavailable_reason",
            "sentence-transformers installed but runtime dependency failed: torchcodec mismatch",
        )

        result = _memory_connection.check_embeddings_status()

        advisory = str(result["advisory"])
        assert "runtime dependency failed" in advisory
        assert "torchcodec mismatch" in advisory

    def test_check_embeddings_status_has_required_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """check_embeddings_status always returns enabled, available, advisory keys."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state import memory_adapter

        mock_config = TRWConfig.__new__(TRWConfig)
        object.__setattr__(mock_config, "embeddings_enabled", False)
        monkeypatch.setattr(memory_adapter, "get_config", lambda: mock_config)

        result = memory_adapter.check_embeddings_status()

        assert "enabled" in result
        assert "available" in result
        assert "advisory" in result
