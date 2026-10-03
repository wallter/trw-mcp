"""INC-147 visibility: a persistent push failure is one clear line in trw_status and doctor.

Before, a backend that refused one learning produced a single warning log line
and nothing an operator reads: the failure counter climbed without the
server's reason, and refused entries were invisible. ``step_sync_health`` is
the one read behind session start, ``trw_status`` and ``trw-mcp doctor``, so
the count of held-back learnings and the server's reason are reported there.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.server import _doctor_sync_health
from trw_mcp.tools._sync_health import step_sync_health

pytestmark = [pytest.mark.integration]


def _state(trw_dir: Path, **fields: object) -> None:
    trw_dir.mkdir(parents=True, exist_ok=True)
    base: dict[str, object] = {
        "last_push_at": datetime.now(tz=timezone.utc).isoformat(),
        "consecutive_failures": 0,
    }
    base.update(fields)
    (trw_dir / "sync-state.json").write_text(json.dumps(base), encoding="utf-8")


_HELD = {
    "L-1": {"sync_seq": 3, "reason": "HTTP 422: type: Input should be 'pattern'", "at": "2026-10-01T10:00:00+00:00"},
    "L-2": {"sync_seq": 5, "reason": "HTTP 422: type: Input should be 'pattern'", "at": "2026-10-01T11:00:00+00:00"},
}


def test_held_back_learnings_degrade_the_read_with_count_reason_and_the_retry_command(tmp_path: Path) -> None:
    _state(tmp_path, rejected_entries=_HELD)

    health = step_sync_health(tmp_path, TRWConfig())

    assert health["degraded"] is True
    assert health["rejected"] == 2
    advisory = str(health["advisory"])
    assert "2 learning(s)" in advisory
    assert "HTTP 422: type: Input should be 'pattern'" in advisory
    assert "trw-mcp sync push --retry-rejected" in advisory
    assert "\n" not in advisory


def test_consecutive_failures_name_the_servers_last_error(tmp_path: Path) -> None:
    _state(tmp_path, consecutive_failures=12, last_error="push failed: 3 entries (HTTP 503: maintenance window)")

    health = step_sync_health(tmp_path, TRWConfig())

    assert health["degraded"] is True
    assert "12 consecutive failures" in str(health["advisory"])
    assert "HTTP 503: maintenance window" in str(health["advisory"])


def test_a_healthy_read_reports_no_rejections(tmp_path: Path) -> None:
    _state(tmp_path)

    health = step_sync_health(tmp_path, TRWConfig())

    assert health["degraded"] is False
    assert "rejected" not in health  # omitted when there is nothing to act on


def test_doctor_warns_in_one_line_with_the_count_and_reason(tmp_path: Path) -> None:
    _state(tmp_path, rejected_entries=_HELD)

    status, message = _doctor_sync_health.sync_health_row(tmp_path, TRWConfig())

    assert status == "WARN"
    assert "2 learning(s)" in message
    assert "HTTP 422" in message


def test_trw_status_carries_the_rejected_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.state import _paths
    from trw_mcp.tools._orchestration_status_assembly import _apply_sync_push_field

    _state(tmp_path, rejected_entries=_HELD)
    monkeypatch.setattr(_paths, "resolve_trw_dir", lambda: tmp_path)
    result: dict[str, object] = {}

    _apply_sync_push_field(result)  # type: ignore[arg-type]

    sync_push = result["sync_push"]
    assert isinstance(sync_push, dict)
    assert sync_push["rejected"] == 2
    assert "HTTP 422" in sync_push["advisory"]
