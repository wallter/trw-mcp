"""Unit tests for :mod:`trw_mcp.security.anomaly_detector` (PRD-INFRA-SEC-001 FR-3/4)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from trw_mcp.security.anomaly_detector import (
    DEFAULT_SIGMA_THRESHOLD,
    DEFAULT_WINDOW_SECONDS,
    SHADOW_WINDOW_DAYS,
    AnomalyDetector,
    AnomalyDetectorConfig,
    AnomalyObservation,
    hash_tool_args,
)

pytestmark = pytest.mark.integration


def _cfg(tmp_path: Path, *, sigma: float = DEFAULT_SIGMA_THRESHOLD) -> AnomalyDetectorConfig:
    return AnomalyDetectorConfig(
        sigma_threshold=sigma,
        window_seconds=DEFAULT_WINDOW_SECONDS,
        shadow_clock_path=tmp_path / "security" / "mcp_shadow_start.yaml",
    )


def test_hash_tool_args_is_deterministic_and_stable() -> None:
    """FR-4: args_hash must be deterministic under key reorder."""
    h1 = hash_tool_args({"a": 1, "b": 2})
    h2 = hash_tool_args({"b": 2, "a": 1})
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_shadow_clock_bootstraps_idempotently(tmp_path: Path) -> None:
    """Deliverable #7: shadow clock writes once, then returns existing contents."""
    cfg = _cfg(tmp_path)
    det = AnomalyDetector(config=cfg, run_dir=None, fallback_dir=tmp_path)
    assert cfg.shadow_clock_path.exists()
    original = yaml.safe_load(cfg.shadow_clock_path.read_text())
    # Instantiate a second detector — should NOT overwrite.
    AnomalyDetector(config=cfg, run_dir=None, fallback_dir=tmp_path)
    again = yaml.safe_load(cfg.shadow_clock_path.read_text())
    assert original == again
    assert original["phase"] == "shadow"
    # threshold_review_at should be ~21 days after started_at
    started = datetime.fromisoformat(original["started_at"])
    review = datetime.fromisoformat(original["threshold_review_at"])
    assert (review - started).days == SHADOW_WINDOW_DAYS
    _ = det


def test_rate_spike_fires_at_configured_sigma(tmp_path: Path) -> None:
    """FR-3: a 10x burst over a tight baseline fires shadow_anomaly."""
    cfg = _cfg(tmp_path, sigma=3.0)
    det = AnomalyDetector(config=cfg, run_dir=None, fallback_dir=tmp_path)
    # Baseline of low-rate samples: mean=1, stdev≈0.5
    det.seed_baseline(
        known_pairs={("trw", "trw_recall")},
        historical_rates={("trw", "trw_recall"): [1.0, 1.0, 2.0, 1.0, 1.0, 2.0, 1.0, 1.0]},
    )
    now = datetime.now(tz=timezone.utc)
    # Inject 30 calls in the current window → rate=30, should far exceed 3σ
    fired: list[str] = []
    for i in range(30):
        obs = AnomalyObservation(
            ts=now + timedelta(seconds=i * 0.1),
            server="trw",
            tool="trw_recall",
            session_id="test",
        )
        fired.extend(det.observe(obs))
    assert "rate_spike" in fired


def test_silent_on_normal_traffic(tmp_path: Path) -> None:
    """FR-3: a single call under a populated baseline does NOT fire rate_spike."""
    cfg = _cfg(tmp_path, sigma=DEFAULT_SIGMA_THRESHOLD)
    det = AnomalyDetector(config=cfg, run_dir=None, fallback_dir=tmp_path)
    det.seed_baseline(
        known_pairs={("trw", "trw_recall")},
        historical_rates={("trw", "trw_recall"): [5.0, 5.0, 5.0, 5.0, 5.0, 5.0]},
    )
    now = datetime.now(tz=timezone.utc)
    fired = det.observe(AnomalyObservation(ts=now, server="trw", tool="trw_recall", session_id="s"))
    assert "rate_spike" not in fired


def test_first_observation_after_deploy_fires(tmp_path: Path) -> None:
    """FR-4: a (server,tool) pair absent from baseline fires the anomaly."""
    cfg = _cfg(tmp_path)
    det = AnomalyDetector(config=cfg, run_dir=None, fallback_dir=tmp_path)
    det.seed_baseline(known_pairs={("trw", "trw_recall")})
    fired = det.observe(
        AnomalyObservation(
            ts=datetime.now(tz=timezone.utc),
            server="unknown",
            tool="exec_shell",
            session_id="s",
        )
    )
    assert "first_observation_after_deploy" in fired


def test_first_observation_after_deploy_fires_only_once_per_pair(tmp_path: Path) -> None:
    """The pair must be added to baseline after detection so it stops firing.

    Regression: without this, the anomaly detector wrote a shadow event on
    every single tool call (including high-frequency hot paths like
    trw_session_start), generating disk I/O and log noise on every request.
    """
    cfg = _cfg(tmp_path)
    det = AnomalyDetector(config=cfg, run_dir=None, fallback_dir=tmp_path)

    obs = AnomalyObservation(
        ts=datetime.now(tz=timezone.utc),
        server="trw",
        tool="trw_session_start",
        session_id="s",
    )
    first = det.observe(obs)
    second = det.observe(obs)
    third = det.observe(obs)

    assert "first_observation_after_deploy" in first
    assert "first_observation_after_deploy" not in second
    assert "first_observation_after_deploy" not in third


def test_namespace_mismatch_detected(tmp_path: Path) -> None:
    """CVE-2025-53773: prefix that doesn't belong to server fires mismatch."""
    cfg = _cfg(tmp_path)
    det = AnomalyDetector(config=cfg, run_dir=None, fallback_dir=tmp_path)
    det.seed_baseline(known_pairs={("trw", "github__create_issue")})
    fired = det.observe(
        AnomalyObservation(
            ts=datetime.now(tz=timezone.utc),
            server="trw",
            tool="github__create_issue",
            session_id="s",
        )
    )
    assert "namespace_mismatch" in fired


def test_never_raises_on_degenerate_baseline(tmp_path: Path) -> None:
    """Observe-mode invariant: detector NEVER raises, even on bad inputs."""
    cfg = _cfg(tmp_path)
    det = AnomalyDetector(config=cfg, run_dir=None, fallback_dir=tmp_path)
    # No baseline seeded → only first_observation may fire; no crash.
    det.observe(
        AnomalyObservation(
            ts=datetime.now(tz=timezone.utc),
            server="trw",
            tool="trw_recall",
            session_id="s",
        )
    )


def test_emits_via_unified_events(tmp_path: Path) -> None:
    """Anomalies are appended to events-YYYY-MM-DD.jsonl under fallback_dir."""
    cfg = _cfg(tmp_path)
    det = AnomalyDetector(config=cfg, run_dir=None, fallback_dir=tmp_path)
    det.seed_baseline(known_pairs={("trw", "known_tool")})
    det.observe(
        AnomalyObservation(
            ts=datetime.now(tz=timezone.utc),
            server="trw",
            tool="brand_new_tool",
            session_id="s",
            args_hash=hash_tool_args({"a": 1}),
        )
    )
    events_files = list(tmp_path.glob("events-*.jsonl"))
    assert len(events_files) == 1
    content = events_files[0].read_text()
    assert "shadow_anomaly" in content
    assert "first_observation_after_deploy" in content


def test_baseline_arg_hashes_bounded_per_pair(tmp_path: Path) -> None:
    """Novel-arg flooding must not grow the in-memory baseline without bound."""
    cfg = AnomalyDetectorConfig(
        sigma_threshold=DEFAULT_SIGMA_THRESHOLD,
        window_seconds=DEFAULT_WINDOW_SECONDS,
        shadow_clock_path=tmp_path / "security" / "clock.yaml",
        max_arg_hashes_per_pair=8,
    )
    det = AnomalyDetector(config=cfg, run_dir=None, fallback_dir=tmp_path)
    det.seed_baseline(known_pairs={("trw", "flood_tool")})
    now = datetime.now(tz=timezone.utc)
    for i in range(100):
        det.observe(
            AnomalyObservation(
                ts=now + timedelta(seconds=i * 0.01),
                server="trw",
                tool="flood_tool",
                session_id="s",
                args_hash=hash_tool_args({"n": i}),
            )
        )
    bucket = det._baseline_arg_hashes[("trw", "flood_tool")]
    assert len(bucket) == 8  # capped, oldest evicted


def test_baseline_eviction_lets_old_arg_pattern_refire(tmp_path: Path) -> None:
    """Once an arg-hash is evicted, re-seeing it counts as novel again
    (proves eviction is real, not just a length clamp on a stale set)."""
    cfg = AnomalyDetectorConfig(
        sigma_threshold=DEFAULT_SIGMA_THRESHOLD,
        window_seconds=DEFAULT_WINDOW_SECONDS,
        shadow_clock_path=tmp_path / "security" / "clock.yaml",
        max_arg_hashes_per_pair=2,
    )
    det = AnomalyDetector(config=cfg, run_dir=None, fallback_dir=tmp_path)
    det.seed_baseline(known_pairs={("trw", "t")})
    now = datetime.now(tz=timezone.utc)
    first_hash = hash_tool_args({"k": "first"})

    def obs(h: str, i: int) -> list[str]:
        return det.observe(
            AnomalyObservation(
                ts=now + timedelta(seconds=i),
                server="trw",
                tool="t",
                session_id="s",
                args_hash=h,
            )
        )

    assert "novel_arg_pattern" in obs(first_hash, 0)
    # Push 2 more distinct hashes → evicts first_hash (cap=2)
    obs(hash_tool_args({"k": "second"}), 1)
    obs(hash_tool_args({"k": "third"}), 2)
    # first_hash was evicted, so it is novel again
    assert "novel_arg_pattern" in obs(first_hash, 3)


def test_baseline_store_file_rolls_at_cap(tmp_path: Path) -> None:
    """The append-only baseline store must roll to its tail, not grow forever."""
    baseline_path = tmp_path / "security" / "mcp_arg_baseline.jsonl"
    cfg = AnomalyDetectorConfig(
        sigma_threshold=DEFAULT_SIGMA_THRESHOLD,
        window_seconds=DEFAULT_WINDOW_SECONDS,
        shadow_clock_path=tmp_path / "security" / "clock.yaml",
        baseline_store_path=baseline_path,
        max_baseline_store_lines=10,
    )
    det = AnomalyDetector(config=cfg, run_dir=None, fallback_dir=tmp_path)
    det.seed_baseline(known_pairs={("trw", "t")})
    now = datetime.now(tz=timezone.utc)
    for i in range(50):
        det.observe(
            AnomalyObservation(
                ts=now + timedelta(seconds=i * 0.01),
                server="trw",
                tool="t",
                session_id="s",
                args_hash=hash_tool_args({"n": i}),
            )
        )
    lines = baseline_path.read_text().splitlines()
    assert len(lines) <= 10


def test_mode_property_reflects_config(tmp_path: Path) -> None:
    """The public ``mode`` property exposes the configured mode without reaching
    into the private ``_config`` (de-brittling the middleware coupling)."""
    shadow_det = AnomalyDetector(config=_cfg(tmp_path), run_dir=None, fallback_dir=tmp_path)
    assert shadow_det.mode == "shadow"

    enforce_cfg = AnomalyDetectorConfig(
        mode="enforce",
        sigma_threshold=DEFAULT_SIGMA_THRESHOLD,
        window_seconds=DEFAULT_WINDOW_SECONDS,
        shadow_clock_path=tmp_path / "security" / "enforce_clock.yaml",
    )
    enforce_det = AnomalyDetector(config=enforce_cfg, run_dir=None, fallback_dir=tmp_path)
    assert enforce_det.mode == "enforce"


def test_config_rejects_unknown_mode(tmp_path: Path) -> None:
    """A typo'd mode must surface as a validation error, not silently coerce."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        AnomalyDetectorConfig(
            mode="enfroce",  # typo
            shadow_clock_path=tmp_path / "security" / "clock.yaml",
        )


def test_novel_arg_pattern_uses_persisted_baseline_not_process_local_first_seen(
    tmp_path: Path,
) -> None:
    baseline_path = tmp_path / "security" / "mcp_arg_baseline.jsonl"
    cfg = AnomalyDetectorConfig(
        sigma_threshold=DEFAULT_SIGMA_THRESHOLD,
        window_seconds=DEFAULT_WINDOW_SECONDS,
        shadow_clock_path=tmp_path / "security" / "mcp_shadow_start.yaml",
        baseline_store_path=baseline_path,
    )
    arg_hash = hash_tool_args({"path": "README.md"})
    first = AnomalyDetector(config=cfg, run_dir=None, fallback_dir=tmp_path)
    assert "novel_arg_pattern" in first.observe(
        AnomalyObservation(
            ts=datetime.now(tz=timezone.utc),
            server="filesystem",
            tool="read_file",
            session_id="session-a",
            run_id="run-a",
            args_hash=arg_hash,
        )
    )
    second = AnomalyDetector(config=cfg, run_dir=None, fallback_dir=tmp_path)
    assert "novel_arg_pattern" not in second.observe(
        AnomalyObservation(
            ts=datetime.now(tz=timezone.utc),
            server="filesystem",
            tool="read_file",
            session_id="session-b",
            run_id="run-b",
            args_hash=arg_hash,
        )
    )
