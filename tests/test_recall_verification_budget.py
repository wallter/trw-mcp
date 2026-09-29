"""CORE-268 retires implicit verification, not merely its ineffective time budget."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._recall_assertion_verification import _verify_assertions


def test_recall_never_scans_writes_or_schedules_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_a: object, **_kw: object) -> None:
        pytest.fail("implicit verification work on recall")

    for path in (
        "trw_memory.lifecycle.verification_pass.run_verification_pass",
        "trw_memory.lifecycle.verification_pass.persist_verification_outcome",
        "trw_memory.lifecycle.verification.verify_assertions",
        "trw_memory.lifecycle.anchor_validation.compute_anchor_validity",
    ):
        monkeypatch.setattr(path, forbidden)
    rows = [
        {"id": "missing", "anchors": [{"file": "never-read.py", "symbol_name": "x"}]},
        {
            "id": "observed",
            "assertions": [{"last_result": False, "last_verified_at": datetime.now(timezone.utc).isoformat()}],
        },
    ]
    out = _verify_assertions(rows, [], TRWConfig(), lambda rows, *_a, **_kw: rows)
    assert out[0]["verification_status"] == "unknown"
    assert out[1]["verification_status"] == "last_known_failure"
    assert "verification_evidence" not in rows[0]
