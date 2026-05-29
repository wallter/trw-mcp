from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import Phase


class TestValidationReflectionQualityException:
    """Lines 900-901: reflection quality check exception is swallowed (best-effort)."""

    def test_review_phase_reflection_quality_exception_swallowed(self, tmp_path: Path) -> None:
        from trw_mcp.state.validation import check_phase_exit

        run_path = tmp_path / "run"
        meta = run_path / "meta"
        meta.mkdir(parents=True)
        (run_path / "reports").mkdir()

        import json

        (meta / "events.jsonl").write_text(
            json.dumps({"event": "reflection_complete", "ts": "2026-01-01T00:00:00Z"}) + "\n"
        )
        (meta / "run.yaml").write_text("status: active\n")
        (run_path / "reports" / "final.md").write_text("# Final Report\n")
        config = TRWConfig(trw_dir=str(tmp_path / ".trw"))

        with (
            patch(
                "trw_mcp.state.analytics.compute_reflection_quality", side_effect=RuntimeError("quality check exploded")
            ),
            patch("trw_mcp.state.validation._best_effort_integration_check"),
        ):
            result = check_phase_exit(Phase.REVIEW, run_path, config)

        assert result is not None
        assert hasattr(result, "valid")
        assert isinstance(result.failures, list)


class TestValidationDeliverRunYamlReadException:
    """Lines 920-921: OSError reading run.yaml in deliver phase is swallowed."""

    def test_deliver_phase_run_yaml_read_exception_swallowed(self, tmp_path: Path) -> None:
        from trw_mcp.state.validation import check_phase_exit

        run_path = tmp_path / "run"
        meta = run_path / "meta"
        meta.mkdir(parents=True)
        (run_path / "reports").mkdir()
        (meta / "run.yaml").write_text("status: active\n")

        import json

        events = [{"event": "trw_claude_md_sync_complete", "ts": "2026-01-01T00:00:00Z"}]
        (meta / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
        config = TRWConfig(trw_dir=str(tmp_path / ".trw"))

        with (
            patch("trw_mcp.state.persistence.FileStateReader.read_yaml", side_effect=StateError("read failed")),
            patch("trw_mcp.state.validation._best_effort_build_check"),
            patch("trw_mcp.state.validation._best_effort_integration_check"),
        ):
            result = check_phase_exit(Phase.DELIVER, run_path, config)

        assert result is not None
        assert hasattr(result, "valid")
        assert isinstance(result.failures, list)
