"""Integration tests for trw_session_start ceremony flows."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests._ceremony_helpers import make_ceremony_server as _make_ceremony_server


def test_connection_fingerprint_exposes_frozen_loaded_module_identity() -> None:
    from trw_mcp.canons.fingerprint import ProcessFingerprint
    from trw_mcp.tools import _connection_fingerprint as connection

    frozen = ProcessFingerprint(
        schema_version=2,
        trw_mcp_version="1",
        framework_version="1",
        aaref_version="1",
        template_version="1",
        registry_digest="registry",
        source_digests={},
        loaded_module_digest="loaded-bytes",
        surface_digest="surface",
        digest="process",
    )
    with patch("trw_mcp.canons.fingerprint.get_frozen_fingerprint", return_value=frozen):
        block = connection.build_connection_fingerprint()
    assert block["protocol_version"] == "2"
    assert block["process_fingerprint_digest"] == "process"
    assert block["loaded_module_digest"] == "loaded-bytes"


@pytest.mark.integration
class TestSessionStartPartialFailure:
    """trw_session_start resilience when sub-operations fail."""

    def test_returns_result_when_recall_fails(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If recall raises, status step still runs and result is returned.

        Recall is fail-open by contract: a recall-only failure must NOT flip
        ``success`` (which would mislead agents into needless retries). The
        failure is surfaced under the non-fatal ``warnings`` channel instead.
        """
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch(
                "trw_mcp.tools.ceremony.resolve_trw_dir",
                side_effect=Exception("recall boom"),
            ),
            patch(
                "trw_mcp.tools.ceremony.find_active_run",
                return_value=None,
            ),
        ):
            result = tools["trw_session_start"].fn()

        # Recall-only failure no longer flips success; it lands in warnings.
        assert result["success"] is True
        assert "run" in result
        warnings = result.get("warnings", [])
        assert any("recall" in w for w in warnings), f"expected recall warning, got {warnings}"
        assert "recall" not in " ".join(result.get("errors", []))

    def test_returns_result_when_status_fails(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If status check raises, recall still runs and result is returned."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch(
                "trw_mcp.tools.ceremony.find_active_run",
                side_effect=Exception("status boom"),
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert result["success"] is False
        assert any("status" in error for error in result["errors"])
        assert "learnings" in result

    def test_success_when_all_steps_work(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Both recall and status succeed."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        ):
            result = tools["trw_session_start"].fn()

        assert result["success"] is True
        assert result["errors"] == []
        assert "timestamp" in result

    def test_session_start_repopulates_injected_learning_ids(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """surfaced session_start learnings must seed the injected-ID state file."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        injected_file = trw_dir / "context" / "injected_learning_ids.txt"

        surfaced = [
            {"id": "L-session-1", "summary": "First surfaced learning"},
            {"id": "L-session-2", "summary": "Second surfaced learning"},
        ]

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.tools._ceremony_helpers.perform_session_recalls",
                return_value=(surfaced, False, {}),
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert result["success"] is True
        assert injected_file.read_text(encoding="utf-8").splitlines() == [
            "L-session-1",
            "L-session-2",
        ]

    def test_injected_ids_file_is_bounded_and_deduped(self, tmp_path: Path) -> None:
        """_write_session_start_ids must cap the file size and de-dup IDs so it
        cannot grow without limit across sessions."""
        from trw_mcp.tools import _ceremony_session_start_steps as steps

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        cap = steps._MAX_INJECTED_IDS

        # Simulate many sessions each surfacing a fresh batch of unique IDs.
        total = cap * 3
        for i in range(total):
            steps._write_session_start_ids(trw_dir, [{"id": f"L-{i}"}])

        injected_file = trw_dir / "context" / "injected_learning_ids.txt"
        lines = [ln for ln in injected_file.read_text(encoding="utf-8").splitlines() if ln]
        assert len(lines) == cap, f"file not capped: {len(lines)} lines"
        # The most recent IDs are retained (recency tail).
        assert lines[-1] == f"L-{total - 1}"
        assert f"L-{total - cap}" in lines
        # An old ID well outside the window was evicted.
        assert "L-0" not in lines

    def test_injected_ids_dedup_preserves_recency(self, tmp_path: Path) -> None:
        """Re-surfacing an existing ID does not duplicate it; recency wins."""
        from trw_mcp.tools import _ceremony_session_start_steps as steps

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)

        steps._write_session_start_ids(trw_dir, [{"id": "A"}, {"id": "B"}])
        steps._write_session_start_ids(trw_dir, [{"id": "A"}])  # re-surface A

        injected_file = trw_dir / "context" / "injected_learning_ids.txt"
        lines = [ln for ln in injected_file.read_text(encoding="utf-8").splitlines() if ln]
        assert lines == ["B", "A"], f"expected dedup with A moved to most-recent, got {lines}"

    def test_session_start_returns_assertion_health(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Assertion health is exposed through the production trw_session_start tool path."""
        from datetime import datetime, timedelta, timezone
        from unittest.mock import MagicMock

        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        mock_backend = MagicMock()
        mock_backend.entries_with_assertions.return_value = [
            MagicMock(
                assertions=[
                    MagicMock(last_result=True, last_verified_at=recent),
                    MagicMock(last_result=False, last_verified_at=recent),
                ]
            ),
            MagicMock(
                assertions=[
                    MagicMock(last_result=None, last_verified_at=None),
                    MagicMock(last_result=None, last_verified_at=recent),
                ]
            ),
        ]

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.get_backend", return_value=mock_backend),
        ):
            # verbose=True: assertion_health is a diagnostic sub-block that
            # compact-by-default (PRD-IMPROVE-MCP-04) folds into health_summary.
            result = tools["trw_session_start"].fn(verbose=True)

        assert result["success"] is True
        assert result["assertion_health"] == {
            "passing": 1,
            "failing": 1,
            "stale": 1,
            "unverifiable": 1,
            "total": 2,
        }


_FR01_REQUIRED_FIELDS = (
    "protocol_version",
    "build_identity",
    "project_identity",
    "connection_nonce",
    "result_schema",
    "transport",
    "owner_status_capability",
    "request_identity_capability",
    "process_fingerprint_digest",
    "loaded_module_digest",
)
# Server-identity fields are stable within a process (only the nonce is
# per-process). The nonce + schema are also stable across same-process calls.
_FR01_SERVER_IDENTITY_FIELDS = (
    "protocol_version",
    "build_identity",
    "project_identity",
    "result_schema",
    "transport",
    "process_fingerprint_digest",
    "loaded_module_digest",
)


@pytest.mark.integration
class TestPrdCore215Fr01:
    """PRD-CORE-215 FR01 — session-start connection fingerprint and capabilities."""

    def test_prd_core_215_fr01(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import importlib

        from trw_mcp.tools import _connection_fingerprint as fp

        # --- Completeness: a fingerprint carries every FR01 field, populated. ---
        first = fp.build_connection_fingerprint()
        for field in _FR01_REQUIRED_FIELDS:
            assert field in first, f"missing fingerprint field {field}"
        assert first["protocol_version"]
        assert first["build_identity"]
        assert first["project_identity"]
        assert first["connection_nonce"]
        assert first["result_schema"]
        # Owner-status + request-identity capabilities are advertised.
        assert first["owner_status_capability"] is True
        assert first["request_identity_capability"] is True

        # --- Same-process calls preserve server identity, nonce, and schema. ---
        second = fp.build_connection_fingerprint()
        assert second["connection_nonce"] == first["connection_nonce"]
        assert second["result_schema"] == first["result_schema"]
        for field in _FR01_SERVER_IDENTITY_FIELDS:
            assert second[field] == first[field]

        # --- Negative: no field may claim proxy / shared-server identity. ---
        assert first["transport"] == "stdio"
        for value in first.values():
            if isinstance(value, str):
                lowered = value.lower()
                assert "proxy" not in lowered
                assert "shared" not in lowered
                assert "http" not in lowered

        # --- New process simulation: reload yields a *new* nonce, same schema. ---
        old_nonce = fp._CONNECTION_NONCE
        try:
            reloaded = importlib.reload(fp)
            fresh = reloaded.build_connection_fingerprint()
            assert fresh["connection_nonce"] != old_nonce, "nonce not regenerated per process"
            # Server identity (schema/transport/protocol) is unchanged across processes.
            assert fresh["result_schema"] == first["result_schema"]
            assert fresh["transport"] == "stdio"
        finally:
            importlib.reload(fp)

        # --- Missing package metadata falls back typed, never raises. ---
        import importlib.metadata as _md

        def _boom(_name: str) -> str:
            raise _md.PackageNotFoundError("trw-mcp")

        monkeypatch.setattr(_md, "version", _boom)
        degraded = fp.build_connection_fingerprint()
        assert isinstance(degraded["build_identity"], str)
        assert degraded["build_identity"]  # typed fallback, not an exception
        monkeypatch.undo()

        # --- Production path: the finalizer emits the block on the tool result. ---
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        ):
            result = tools["trw_session_start"].fn()
            result_again = tools["trw_session_start"].fn()

        block = result["connection_fingerprint"]
        assert isinstance(block, dict)
        for field in _FR01_REQUIRED_FIELDS:
            assert field in block
        assert block["transport"] == "stdio"
        # Two same-process production calls preserve nonce + server identity.
        assert result_again["connection_fingerprint"]["connection_nonce"] == block["connection_nonce"]
        assert result_again["connection_fingerprint"]["result_schema"] == block["result_schema"]


@pytest.mark.integration
class TestSessionStartUpdateAdvisory:
    """Verify check_for_update() wiring in trw_session_start."""

    def test_update_advisory_included_when_update_available(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When check_for_update returns available=True, advisory is in results."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={
                    "available": True,
                    "current": "0.4.0",
                    "latest": "0.5.0",
                    "channel": "latest",
                    "advisory": "TRW v0.5.0 available (you have v0.4.0). ",
                },
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert "update_advisory" in result
        assert "0.5.0" in str(result["update_advisory"])

    def test_no_update_advisory_when_up_to_date(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When check_for_update returns available=False, advisory key is absent."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={
                    "available": False,
                    "current": "0.5.0",
                    "latest": "0.5.0",
                    "channel": "latest",
                    "advisory": None,
                },
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert result.get("update_advisory") is None

    def test_update_check_failure_is_fail_open(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If check_for_update raises, session start still succeeds."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                side_effect=Exception("network boom"),
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert "update_advisory" not in result or result.get("update_advisory") is None


@pytest.mark.integration
class TestSessionStartPayloadTrimming:
    """PRD-IMPROVE-MCP-04 FR1 — compact-by-default vs verbose, end-to-end."""

    def test_default_is_compact_with_health_summary(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        ):
            result = tools["trw_session_start"].fn()

        # Compact mode: flag set, diagnostic blocks folded into a summary,
        # token estimate present, load-bearing fields intact.
        assert result["compact"] is True
        assert "health_summary" in result
        assert "embed_health" not in result
        assert "step_durations_ms" not in result
        assert isinstance(result["payload_token_estimate"], int)
        assert result["payload_token_estimate"] > 0
        assert "run" in result
        assert "framework_reminder" in result
        assert "errors" in result

    def test_verbose_returns_full_payload(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        ):
            result = tools["trw_session_start"].fn(verbose=True)

        # Verbose mode: full diagnostic payload, no summary collapse.
        assert result["compact"] is False
        assert "health_summary" not in result
        assert "embed_health" in result
        assert "step_durations_ms" in result
        # Run/pin + framework reminder still present.
        assert "run" in result
        assert "framework_reminder" in result
        assert "timestamp" in result
