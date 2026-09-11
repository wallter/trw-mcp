"""CORE268 evidence qualification latency and deterministic no-inline-work proof.

Retains the 10,000-row/25-result/30-sample legacy microbenchmark, but cache TTL
no longer authorizes a verifier. This is not total recall latency or efficacy.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from trw_memory.models.memory import Anchor, MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._maintain_verify import run_maintain_verify
from trw_mcp.tools._recall_impl import _verify_assertions


def forbid_work(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("evidence-only recall invoked verification/cache/write work")

    monkeypatch.setattr("trw_mcp.tools._verification_pass.run_verification_pass", forbidden)
    monkeypatch.setattr("trw_mcp.tools._verification_pass.persist_verification_outcome", forbidden)
    monkeypatch.setattr("trw_mcp.tools._verification_cache.warm_verified_verdict", forbidden)
    monkeypatch.setattr("trw_mcp.state.memory_adapter.get_backend", forbidden)


@pytest.mark.slow
@pytest.mark.xdist_group(name="recall_verification_latency")
def test_recall_verification_p95_within_budget(tmp_path: Path, monkeypatch):
    backend = SQLiteBackend(tmp_path / "memory.db")
    try:
        backend.store_many([MemoryEntry(id=f"L-filler-{i}", content="filler") for i in range(10_000)])
        (tmp_path / "mod.py").write_text("def anchored_symbol(): pass\n")
        backend.store_many(
            [
                MemoryEntry(
                    id=f"L-evidence-{i}",
                    content="claim",
                    anchors=[Anchor(file="mod.py", symbol_name="anchored_symbol")],
                )
                for i in range(25)
            ]
        )
        run_maintain_verify(
            backend,
            assertion_failure_penalty=0.15,
            assertion_stale_threshold_days=7,
            anchor_validity_verified_floor=0.8,
            batch_limit=10,
            project_root=tmp_path,
        )
        entries = [backend.get(f"L-evidence-{i}", namespace="default").model_dump(mode="json") for i in range(25)]
        assert all(entry["verification_checked_at"] for entry in entries)
        forbid_work(monkeypatch)
        rank = MagicMock(side_effect=lambda rows, *a, **k: rows)
        samples = []
        for _ in range(30):
            started = time.perf_counter()
            results = _verify_assertions(entries, [], TRWConfig(), rank)
            samples.append((time.perf_counter() - started) * 1000)
            assert all(row["verification_status"] == "last_known_pass" for row in results)
            assert all(row["verification_evidence"]["current_tree_verified"] is False for row in results)
        p95 = sorted(samples)[int(len(samples) * 0.95) - 1]
        assert p95 < 40.0, f"evidence qualification p95={p95:.2f}ms, samples={samples}"
    finally:
        backend.close()


@pytest.mark.parametrize(
    "stamp",
    [None, "bad", (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()],
    ids=["missing", "malformed", "expired"],
)
def test_cold_or_expired_evidence_does_not_trigger_real_pass(stamp, monkeypatch):
    forbid_work(monkeypatch)
    rank = MagicMock(side_effect=lambda rows, *a, **k: rows)
    results = _verify_assertions(
        [
            {
                "id": "L-cold",
                "verification_status": "verified",
                "verification_checked_at": stamp,
                "anchors": [{"file": "mod.py", "symbol_name": "x"}],
            }
        ],
        [],
        TRWConfig(verification_cache_ttl_seconds=3600),
        rank,
    )
    evidence = results[0]["verification_evidence"]
    assert evidence["current_tree_verified"] is False
    assert evidence["aggregate"]["freshness"] == ("expired" if stamp not in (None, "bad") else "unknown")
