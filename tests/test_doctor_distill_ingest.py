"""``distill_ingest`` row of ``trw-mcp doctor`` (2026-09-27 audit touchpoint #3).

Reads ``<repo>/.trw/distill/ingest-status.json``, the current-state receipt
``trw_distill.cli._ingest_status`` writes on every preflight outcome of
``trw-distill run --incremental --live-ingest``. Zero ``trw_distill``
import — every case here writes the JSON file directly, mirroring the real
IP boundary the row itself respects.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.server._doctor_checks_registry import CHECKS
from trw_mcp.server._doctor_distill_ingest import INGEST_STATUS_REL, distill_ingest_row
from trw_mcp.server._subcommands_doctor import _check_distill_ingest

pytestmark = pytest.mark.unit


def _write_status(repo: Path, payload: dict[str, object]) -> None:
    target = repo / INGEST_STATUS_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")


def test_skip_when_no_ingest_has_ever_run(tmp_path: Path) -> None:
    status, message = distill_ingest_row(tmp_path, TRWConfig())
    assert status == "SKIP"
    assert "no distill incremental-ingest run" in message


def test_warn_on_preflight_failure_names_flag_and_fix(tmp_path: Path) -> None:
    _write_status(
        tmp_path,
        {
            "outcome": "preflight_failed",
            "checked_at": "2026-09-27T00:00:00Z",
            "trigger": "post_commit",
            "failed_checks": ["ollama_primary_model_installed"],
            "missing_models": ["qwen3.6:35b-a3b"],
            "fix_commands": ["ollama pull qwen3.6:35b-a3b"],
            "flag": "post_commit_distill_incremental",
        },
    )
    status, message = distill_ingest_row(tmp_path, TRWConfig())
    assert status == "WARN"
    assert "qwen3.6:35b-a3b" in message
    assert "ollama pull qwen3.6:35b-a3b" in message
    assert "post_commit_distill_incremental" in message


def test_pass_when_last_outcome_was_ok(tmp_path: Path) -> None:
    _write_status(tmp_path, {"outcome": "ok", "checked_at": "2026-09-27T00:00:00Z", "trigger": "post_commit"})
    status, _message = distill_ingest_row(tmp_path, TRWConfig())
    assert status == "PASS"


def test_warn_on_malformed_json(tmp_path: Path) -> None:
    target = tmp_path / INGEST_STATUS_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("not json", encoding="utf-8")
    status, _message = distill_ingest_row(tmp_path, TRWConfig())
    assert status == "WARN"


def test_check_distill_ingest_is_registered_and_wired(tmp_path: Path) -> None:
    """Wired-not-just-implemented (FAST-RULES UPDATE 2): the row is in the CHECKS registry."""
    names = [entry[0] for entry in CHECKS]
    assert "distill_ingest" in names
    result = _check_distill_ingest(tmp_path, TRWConfig())
    assert result.name == "distill_ingest"
    assert result.status == "SKIP"
