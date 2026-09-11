"""CORE-268 retires implicit verification, not merely its ineffective time budget."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._recall_assertion_verification import _verify_assertions


@pytest.mark.parametrize("budget", [0, 1, 1000])
def test_recall_never_scans_writes_or_schedules_verification(monkeypatch: pytest.MonkeyPatch, budget: int) -> None:
    def forbidden(*_a: object, **_kw: object) -> None:
        pytest.fail("implicit verification work on recall")

    for path in (
        "trw_mcp.tools._verification_pass.run_verification_pass",
        "trw_mcp.tools._verification_pass.persist_verification_outcome",
        "trw_mcp.tools._verification_cache.warm_verified_verdict",
        "trw_memory.lifecycle.verification.verify_assertions",
        "trw_memory.lifecycle.anchor_validation.compute_anchor_validity",
        "trw_mcp.scoring.apply_contradiction_penalty",
    ):
        monkeypatch.setattr(path, forbidden)
    rows = [
        {"id": "missing", "anchors": [{"file": "never-read.py", "symbol_name": "x"}]},
        {
            "id": "observed",
            "assertions": [{"last_result": False, "last_verified_at": datetime.now(timezone.utc).isoformat()}],
        },
    ]
    out = _verify_assertions(rows, [], TRWConfig(recall_verification_budget_ms=budget), lambda rows, *_a, **_kw: rows)
    assert out[0]["verification_status"] == "unknown"
    assert out[1]["verification_status"] == "last_known_failure"
    assert "verification_evidence" not in rows[0]
