"""PRD-CORE-192 — review_gate_mode warn->block escalation + pre-deliver REVIEW nudge.

These tests assert the FR01-FR05 + NFR02 contract:

- FR01: ``review_gate_mode`` config field accepts ``warn``/``block``, defaults to
  ``warn``, rejects anything else.
- FR02: STANDARD/COMPREHENSIVE + no review.yaml + ``review_gate_mode=block`` ->
  ``check_delivery_gates`` returns ``review_block`` (not ``review_warning``);
  with ``warn`` (default) it returns ``review_warning`` and no ``review_block``.
- FR03: a delivery refused under FR02 emits a structlog ``review_gate_mode_blocked``
  event carrying ``complexity_class`` + ``review_gate_mode``.
- FR04: a STANDARD+ run with no review.yaml surfaces a ``review_nudge`` referencing
  ``trw_review`` (regardless of mode); a run WITH review.yaml does not.
- FR05: under block mode, ``allow_unverified=True`` + a non-empty reason proceeds
  and sets ``truthfulness_gate_bypassed``.
- NFR02: a config-read exception inside ``_check_review_gate`` fails open to the
  pre-existing warning (not a block).

Real verdict strings, real gate dicts, real deliver success/blocked values are
asserted — the deliver path is not mocked away.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import structlog
from fastmcp import FastMCP
from pydantic import ValidationError

from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools.ceremony import register_ceremony_tools

# ── FR01: config field ────────────────────────────────────────────────────


@pytest.mark.unit
class TestReviewGateModeConfigField:
    def test_default_is_warn(self) -> None:
        config = TRWConfig()
        assert config.review_gate_mode == "warn"

    def test_block_accepted(self) -> None:
        config = TRWConfig(review_gate_mode="block")
        assert config.review_gate_mode == "block"

    def test_invalid_value_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TRWConfig(review_gate_mode="invalid")


# ── FR02/FR04/NFR02: _check_review_gate + check_delivery_gates ─────────────


def _write_gate_run(
    tmp_path: Path,
    *,
    complexity_class: str,
    with_review: bool = False,
) -> Path:
    run_dir = tmp_path / "docs" / "task" / "runs" / "20260611T000000Z-gate"
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        f"run_id: gate\nstatus: active\nphase: deliver\nprd_scope: []\ncomplexity_class: {complexity_class}\n",
        encoding="utf-8",
    )
    if with_review:
        (meta / "review.yaml").write_text(
            "substantive: true\nverdict: pass\ncritical_count: 0\n",
            encoding="utf-8",
        )
    # a single file_modified so the run is non-trivial but below the R-01 threshold
    (meta / "events.jsonl").write_text(
        json.dumps({"ts": "2026-06-11T00:00:00Z", "event": "session_start"})
        + "\n"
        + json.dumps({"ts": "2026-06-11T00:00:01Z", "event": "file_modified", "data": {"path": "src/x.py"}})
        + "\n",
        encoding="utf-8",
    )
    return run_dir


def _config_with_mode(mode: str) -> TRWConfig:
    return TRWConfig(review_gate_mode=mode)


@pytest.mark.integration
class TestEscalation:
    @pytest.mark.parametrize("complexity_class", ["STANDARD", "COMPREHENSIVE"])
    def test_block_mode_escalates_missing_review(self, tmp_path: Path, complexity_class: str) -> None:
        from trw_mcp.tools._delivery_helpers import check_delivery_gates

        run_dir = _write_gate_run(tmp_path, complexity_class=complexity_class)
        with patch("trw_mcp.tools._delivery_helpers.get_config", return_value=_config_with_mode("block")):
            result = check_delivery_gates(run_dir, FileStateReader(), tmp_path / ".trw")

        assert "review_block" in result
        assert result.get("review_warning") is None
        assert "review" in str(result["review_block"]).lower()

    def test_warn_mode_keeps_warning(self, tmp_path: Path) -> None:
        from trw_mcp.tools._delivery_helpers import check_delivery_gates

        run_dir = _write_gate_run(tmp_path, complexity_class="STANDARD")
        with patch("trw_mcp.tools._delivery_helpers.get_config", return_value=_config_with_mode("warn")):
            result = check_delivery_gates(run_dir, FileStateReader(), tmp_path / ".trw")

        assert "review_warning" in result
        assert result.get("review_block") is None

    def test_minimal_complexity_not_escalated(self, tmp_path: Path) -> None:
        from trw_mcp.tools._delivery_helpers import check_delivery_gates

        run_dir = _write_gate_run(tmp_path, complexity_class="MINIMAL")
        with patch("trw_mcp.tools._delivery_helpers.get_config", return_value=_config_with_mode("block")):
            result = check_delivery_gates(run_dir, FileStateReader(), tmp_path / ".trw")

        assert "review_block" not in result
        assert "review_advisory" in result

    def test_config_read_failure_fails_open_to_warning(self, tmp_path: Path) -> None:
        """NFR02 — a config exception falls back to the pre-existing warning, not a block."""
        from trw_mcp.tools._delivery_helpers import check_delivery_gates

        run_dir = _write_gate_run(tmp_path, complexity_class="STANDARD")

        def _boom() -> TRWConfig:
            raise RuntimeError("config unavailable")

        with patch("trw_mcp.tools._delivery_helpers.get_config", side_effect=_boom):
            result = check_delivery_gates(run_dir, FileStateReader(), tmp_path / ".trw")

        assert "review_warning" in result
        assert result.get("review_block") is None

    def test_config_failure_logs_enforcement_degraded_warning(self, tmp_path: Path) -> None:
        """codex cross-model review #6: a config-read failure in the review block-mode

        check must NOT be swallowed silently — it logs an explicit
        ``review_gate_mode_enforcement_degraded`` WARNING so the operator sees the
        gate fell back to soft. Fail-open posture is kept (no spurious block).
        """
        import structlog

        from trw_mcp.tools._delivery_review_gate import _review_gate_mode_is_block

        def _boom() -> TRWConfig:
            raise RuntimeError("config unavailable")

        with (
            patch("trw_mcp.tools._delivery_helpers.get_config", side_effect=_boom),
            structlog.testing.capture_logs() as logs,
        ):
            result = _review_gate_mode_is_block("STANDARD")

        assert result is False, "must fail open to soft warning, never a hard block"
        degraded = [e for e in logs if e.get("event") == "review_gate_mode_enforcement_degraded"]
        assert degraded, f"expected an enforcement-degraded warning; got {logs}"
        assert degraded[0]["log_level"] == "warning"
        assert "degrad" in degraded[0]["reason"].lower() or "soft" in degraded[0]["reason"].lower()


@pytest.mark.integration
class TestNudge:
    def test_nudge_present_when_no_review(self, tmp_path: Path) -> None:
        from trw_mcp.tools._delivery_helpers import check_delivery_gates

        run_dir = _write_gate_run(tmp_path, complexity_class="STANDARD")
        with patch("trw_mcp.tools._delivery_helpers.get_config", return_value=_config_with_mode("warn")):
            result = check_delivery_gates(run_dir, FileStateReader(), tmp_path / ".trw")

        assert "review_nudge" in result
        assert "trw_review" in str(result["review_nudge"])

    def test_nudge_absent_when_review_present(self, tmp_path: Path) -> None:
        from trw_mcp.tools._delivery_helpers import check_delivery_gates

        run_dir = _write_gate_run(tmp_path, complexity_class="STANDARD", with_review=True)
        with patch("trw_mcp.tools._delivery_helpers.get_config", return_value=_config_with_mode("warn")):
            result = check_delivery_gates(run_dir, FileStateReader(), tmp_path / ".trw")

        assert "review_nudge" not in result


# ── FR02/FR03/FR05: real trw_deliver path ─────────────────────────────────


def _make_deliver_fn() -> Callable[..., dict[str, Any]]:
    server = FastMCP("test")
    register_ceremony_tools(server)
    return get_tools_sync(server)["trw_deliver"].fn


def _deliver(tmp_path: Path, run_dir: Path, *, mode: str, **kwargs: Any) -> dict[str, Any]:
    deliver_fn = _make_deliver_fn()
    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "reflections").mkdir(parents=True, exist_ok=True)
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    config = _config_with_mode(mode)
    with (
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
        patch("trw_mcp.tools.ceremony.get_config", return_value=config),
        patch("trw_mcp.tools._delivery_helpers.get_config", return_value=config),
        patch(
            "trw_mcp.tools.ceremony._do_reflect",
            return_value={"status": "success", "events_analyzed": 0, "learnings_produced": 0},
        ),
        patch(
            "trw_mcp.tools._deferred_delivery._do_index_sync",
            return_value={"status": "success", "index": {}, "roadmap": {}},
        ),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
    ):
        return deliver_fn(run_path=str(run_dir), skip_reflect=True, **kwargs)


def _write_deliver_run(tmp_path: Path, complexity_class: str) -> Path:
    """STANDARD run with a passing build event + no review.yaml.

    The run lives under the configured ``runs_root`` (``.trw/runs``) with a
    ``run_id`` equal to the directory name so it satisfies the deliver-path
    run-identity gate (a spoofed/relocated run_path is rejected before gating).
    """
    run_id = "20260611T000000Z-deliver"
    run_dir = tmp_path / ".trw" / "runs" / "task" / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        f"run_id: {run_id}\nstatus: active\nphase: deliver\nprd_scope: []\ncomplexity_class: {complexity_class}\n",
        encoding="utf-8",
    )
    (meta / "events.jsonl").write_text(
        json.dumps({"ts": "2026-06-11T00:00:00Z", "event": "session_start"})
        + "\n"
        + json.dumps({"ts": "2026-06-11T00:00:01Z", "event": "file_modified", "data": {"path": "src/x.py"}})
        + "\n"
        + json.dumps(
            {
                "ts": "2026-06-11T00:00:02Z",
                "event": "build_check_complete",
                "tests_passed": True,
                "static_checks_clean": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return run_dir


@pytest.mark.integration
class TestDeliverBlockMode:
    def test_block_mode_refuses_missing_review(self, tmp_path: Path) -> None:
        run_dir = _write_deliver_run(tmp_path, "STANDARD")
        result = _deliver(tmp_path, run_dir, mode="block")
        assert result["success"] is False
        assert "review_block" in result

    def test_block_mode_emits_log_event(self, tmp_path: Path) -> None:
        run_dir = _write_deliver_run(tmp_path, "STANDARD")
        with structlog.testing.capture_logs() as logs:
            result = _deliver(tmp_path, run_dir, mode="block")
        assert result["success"] is False
        events = [e for e in logs if e.get("event") == "review_gate_mode_blocked"]
        assert events, f"no review_gate_mode_blocked event in {[e.get('event') for e in logs]}"
        ev = events[0]
        assert ev.get("complexity_class") == "STANDARD"
        assert ev.get("review_gate_mode") == "block"

    def test_block_mode_overridden_by_allow_unverified(self, tmp_path: Path) -> None:
        """FR05 wired with CORE-191: the review_block override requires a structured record."""
        from datetime import datetime, timedelta, timezone

        run_dir = _write_deliver_run(tmp_path, "STANDARD")
        future = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
        reason = json.dumps(
            {
                "failed_command": "trw_review",
                "residual_risk": "CI environment has no reviewer available; logic verified manually",
                "owner": "agent-run-ci",
                "expiry_iso": future,
            }
        )
        result = _deliver(
            tmp_path,
            run_dir,
            mode="block",
            allow_unverified=True,
            unverified_reason=reason,
        )
        assert result["success"] is True
        # truthfulness_gate_bypassed echoes the structured record (CORE-191 FR04).
        assert result.get("truthfulness_gate_bypassed")
        record = result.get("acceptable_failure_record")
        assert isinstance(record, dict)
        assert record["owner"] == "agent-run-ci"

    def test_warn_mode_delivers_without_review(self, tmp_path: Path) -> None:
        """NFR01 — default warn mode is unchanged: STANDARD missing-review still delivers."""
        run_dir = _write_deliver_run(tmp_path, "STANDARD")
        result = _deliver(tmp_path, run_dir, mode="warn")
        assert result["success"] is True
        assert "review_block" not in result
        assert "review_warning" in result

    def test_deliver_surfaces_nudge(self, tmp_path: Path) -> None:
        run_dir = _write_deliver_run(tmp_path, "STANDARD")
        result = _deliver(tmp_path, run_dir, mode="warn")
        assert "review_nudge" in result
        assert "trw_review" in str(result["review_nudge"])
