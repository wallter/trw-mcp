"""3-week shadow-mode anomaly detector (PRD-INFRA-SEC-001 FR-3 / FR-4 / NFR-7, -11).

Observes live tool calls and emits ``MCPSecurityEvent`` records to the unified
``events-YYYY-MM-DD.jsonl`` stream (with legacy ``tool_call_events.jsonl``
projection for back-compat) with
``decision="shadow_anomaly"`` for three observation categories:

* **Frequency spikes** — tool-call rolling rate exceeds baseline by ≥ sigma
  threshold over the rolling window (FR-3).
* **First-observation-after-deploy** — a ``(server, tool)`` pair observed in
  the current session but not in the baseline window (FR-4).
* **Tool-namespace mismatch** — tool name advertised with a prefix that does
  not belong to its declared server namespace (CVE-2025-53773 tool-squatting
  class).

v1 is **observe mode only**. The detector NEVER raises, NEVER blocks, NEVER
rate-limits. All decisions are written as ``MCPSecurityEvent`` payloads via
:func:`trw_mcp.telemetry.unified_events.emit`. A 3-week shadow clock is
written idempotently to ``.trw/security/mcp_shadow_start.yaml`` on the first
invocation.

Observations may legitimately be zero during the baseline-collection window;
emission-field NFR-10 population is checked by unit tests with injected
non-zero observations.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.telemetry.event_base import MCPSecurityEvent
from trw_mcp.telemetry.unified_events import emit as emit_unified_event

logger = structlog.get_logger(__name__)

SHADOW_WINDOW_DAYS = 21
DEFAULT_SIGMA_THRESHOLD = 5.0
DEFAULT_WINDOW_SECONDS = 60


class AnomalyObservation(BaseModel):
    """Single tool-call observation fed to the detector.

    Matches the PRD §13.2 envelope subset needed for FR-3/FR-4 detection.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    ts: datetime
    server: str
    tool: str
    args_hash: str = ""
    run_id: str | None = None
    session_id: str = ""


class AnomalyDetectorConfig(BaseModel):
    """Shadow-mode detector configuration (PRD §13.4)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    mode: str = "shadow"
    sigma_threshold: float = Field(default=DEFAULT_SIGMA_THRESHOLD, gt=0.0)
    window_seconds: int = Field(default=DEFAULT_WINDOW_SECONDS, gt=0)
    shadow_clock_path: Path
    baseline_store_path: Path | None = None


def _hash_args(args: dict[str, Any]) -> str:
    """Stable SHA-256 over canonicalized JSON of the args dict (FR-4)."""
    blob = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _ensure_shadow_clock(path: Path, *, now: datetime | None = None) -> dict[str, str]:
    """Idempotent shadow-clock bootstrap at ``path`` (Deliverable #7).

    Writes ``{started_at, phase: "shadow", threshold_review_at}`` on first
    invocation; subsequent invocations return the existing contents.
    """
    now = now or datetime.now(tz=timezone.utc)
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text()) or {}
        except (OSError, yaml.YAMLError):  # justified: boundary, re-bootstrap on corrupt state rather than crash
            logger.warning(
                "mcp_shadow_clock_corrupt_rebootstrapping",
                path=str(path),
                outcome="rewriting",
            )
            raw = {}
        if isinstance(raw, dict) and "started_at" in raw:
            return {str(k): str(v) for k, v in raw.items()}

    payload = {
        "started_at": now.isoformat(),
        "phase": "shadow",
        "threshold_review_at": (now + timedelta(days=SHADOW_WINDOW_DAYS)).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=True))
    logger.info(
        "mcp_shadow_clock_started",
        path=str(path),
        started_at=payload["started_at"],
        threshold_review_at=payload["threshold_review_at"],
        outcome="initialized",
    )
    return payload


def _emit_anomaly(
    *,
    anomaly_type: str,
    server: str,
    tool: str,
    session_id: str,
    run_id: str | None,
    run_dir: Path | None,
    fallback_dir: Path | None,
    extra: dict[str, Any],
) -> bool:
    """Build an :class:`MCPSecurityEvent` and emit via the unified writer.

    Returns True on successful write, False if the writer fail-opens (never
    raises). Anomaly emissions are ``decision="shadow_anomaly"`` — observe
    mode only, never triggers action.
    """
    payload: dict[str, Any] = {
        "decision": "shadow_anomaly",
        "anomaly_type": anomaly_type,
        "server": server,
        "tool": tool,
        "mode": extra.get("mode", "shadow"),
    }
    payload.update(extra)
    event = MCPSecurityEvent(
        session_id=session_id or "shadow",
        run_id=run_id,
        payload=payload,
    )
    ok = emit_unified_event(event, run_dir=run_dir, fallback_dir=fallback_dir)
    logger.info(
        "mcp_anomaly_detected",
        anomaly_type=anomaly_type,
        server=server,
        tool=tool,
        written=ok,
        outcome="shadow_emitted",
    )
    return ok


class AnomalyDetector:
    """Shadow-mode detector — observe only, never raise, never block."""

    def __init__(
        self,
        *,
        config: AnomalyDetectorConfig,
        run_dir: Path | None = None,
        fallback_dir: Path | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._run_dir = run_dir
        self._fallback_dir = fallback_dir
        self._now_fn = now_fn or (lambda: datetime.now(tz=timezone.utc))
        self._rate_window: dict[tuple[str, str], deque[datetime]] = defaultdict(deque)
        self._baseline_rates: dict[tuple[str, str], deque[float]] = defaultdict(lambda: deque(maxlen=32))
        self._baseline_pairs: set[tuple[str, str]] = set()
        self._baseline_arg_hashes: dict[tuple[str, str], set[str]] = defaultdict(set)
        _ensure_shadow_clock(config.shadow_clock_path, now=self._now_fn())
        self._load_arg_hash_baseline()

    def _load_arg_hash_baseline(self) -> None:
        path = self._config.baseline_store_path
        if path is None or not path.exists():
            return
        try:
            lines = path.read_text().splitlines()
        except OSError:
            logger.warning("mcp_arg_baseline_load_failed", path=str(path), outcome="skipped")
            return
        for line in lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            server = row.get("server")
            tool = row.get("tool")
            args_hash = row.get("arg_hash")
            if isinstance(server, str) and isinstance(tool, str) and isinstance(args_hash, str):
                self._baseline_arg_hashes[(server, tool)].add(args_hash)

    def _persist_arg_hash_baseline(self, obs: AnomalyObservation) -> None:
        path = self._config.baseline_store_path
        if path is None or not obs.args_hash:
            return
        payload = {
            "type": "arg_baseline",
            "ts": obs.ts.isoformat(),
            "server": obs.server,
            "tool": obs.tool,
            "arg_hash": obs.args_hash,
            "run_id": obs.run_id or "",
            "session_id": obs.session_id,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def seed_baseline(
        self,
        known_pairs: Iterable[tuple[str, str]],
        *,
        historical_rates: dict[tuple[str, str], Iterable[float]] | None = None,
        historical_arg_hashes: dict[tuple[str, str], Iterable[str]] | None = None,
    ) -> None:
        """Seed baseline pairs + per-pair historical rate samples.

        ``historical_rates`` maps (server, tool) → iterable of per-window
        call counts. Used by tests and by Phase 2 calibration to prime the
        sigma comparison.
        """
        for pair in known_pairs:
            self._baseline_pairs.add(pair)
        if historical_rates:
            for pair, samples in historical_rates.items():
                bucket = self._baseline_rates[pair]
                for val in samples:
                    bucket.append(float(val))
        if historical_arg_hashes:
            for pair, arg_hashes in historical_arg_hashes.items():
                arg_bucket = self._baseline_arg_hashes[pair]
                for arg_hash in arg_hashes:
                    arg_bucket.add(str(arg_hash))

    def _prune(self, key: tuple[str, str], now: datetime) -> None:
        window = self._rate_window[key]
        cutoff = now - timedelta(seconds=self._config.window_seconds)
        while window and window[0] < cutoff:
            window.popleft()

    def _check_rate_spike(self, obs: AnomalyObservation) -> tuple[bool, dict[str, float]]:
        key = (obs.server, obs.tool)
        window = self._rate_window[key]
        window.append(obs.ts)
        self._prune(key, obs.ts)

        current_rate = float(len(window))
        baseline_samples = list(self._baseline_rates.get(key, ()))
        if len(baseline_samples) < 3:
            return False, {
                "current_rate": current_rate,
                "baseline_p99": 0.0,
                "sigma": 0.0,
            }
        mean = statistics.mean(baseline_samples)
        try:
            stdev = statistics.stdev(baseline_samples)
        except statistics.StatisticsError:  # justified: boundary, <2 samples after filter; skip
            return False, {
                "current_rate": current_rate,
                "baseline_p99": mean,
                "sigma": 0.0,
            }
        if stdev <= 0.0 or math.isnan(stdev):
            return False, {
                "current_rate": current_rate,
                "baseline_p99": mean,
                "sigma": 0.0,
            }
        sigma = (current_rate - mean) / stdev
        fires = sigma >= self._config.sigma_threshold
        sorted_samples = sorted(baseline_samples)
        p99_index = max(0, round(0.99 * (len(sorted_samples) - 1)))
        p99 = sorted_samples[p99_index]
        return fires, {
            "current_rate": current_rate,
            "baseline_p99": float(p99),
            "sigma": float(sigma),
        }

    def _check_first_observation(self, obs: AnomalyObservation) -> bool:
        return (obs.server, obs.tool) not in self._baseline_pairs

    @staticmethod
    def _check_namespace_mismatch(obs: AnomalyObservation) -> bool:
        """Tool-namespace mismatch: tool name carries a prefix that does not
        belong to the declared server namespace (CVE-2025-53773 class)."""
        if "__" not in obs.tool:
            return False
        prefix = obs.tool.split("__", 1)[0]
        # The trw namespace advertised by claude-code is ``mcp__trw__`` — the
        # normalized short name is what lives in the allowlist; any tool whose
        # short-name prefix does not match its server field is a mismatch.
        return prefix != obs.server and prefix not in {"mcp", obs.server}

    def observe(self, obs: AnomalyObservation) -> list[str]:
        """Process a single observation; return list of anomaly types emitted."""
        fired: list[str] = []
        spike, rate_fields = self._check_rate_spike(obs)
        if spike:
            _emit_anomaly(
                anomaly_type="rate_spike",
                server=obs.server,
                tool=obs.tool,
                session_id=obs.session_id,
                run_id=obs.run_id,
                run_dir=self._run_dir,
                fallback_dir=self._fallback_dir,
                extra=rate_fields,
            )
            fired.append("rate_spike")
        if self._check_first_observation(obs):
            _emit_anomaly(
                anomaly_type="first_observation_after_deploy",
                server=obs.server,
                tool=obs.tool,
                session_id=obs.session_id,
                run_id=obs.run_id,
                run_dir=self._run_dir,
                fallback_dir=self._fallback_dir,
                extra={"args_hash": obs.args_hash},
            )
            fired.append("first_observation_after_deploy")
        if self._check_namespace_mismatch(obs):
            _emit_anomaly(
                anomaly_type="namespace_mismatch",
                server=obs.server,
                tool=obs.tool,
                session_id=obs.session_id,
                run_id=obs.run_id,
                run_dir=self._run_dir,
                fallback_dir=self._fallback_dir,
                extra={"declared_prefix": obs.tool.split("__", 1)[0]},
            )
            fired.append("namespace_mismatch")
        if obs.args_hash and obs.args_hash not in self._baseline_arg_hashes[(obs.server, obs.tool)]:
            self._baseline_arg_hashes[(obs.server, obs.tool)].add(obs.args_hash)
            self._persist_arg_hash_baseline(obs)
            _emit_anomaly(
                anomaly_type="novel_arg_pattern",
                server=obs.server,
                tool=obs.tool,
                session_id=obs.session_id,
                run_id=obs.run_id,
                run_dir=self._run_dir,
                fallback_dir=self._fallback_dir,
                extra={"args_hash": obs.args_hash, "novel_arg_pattern": True},
            )
            fired.append("novel_arg_pattern")
        return fired


def hash_tool_args(args: dict[str, Any]) -> str:
    """Public wrapper over the internal SHA-256 arg-hash (FR-4)."""
    return _hash_args(args)


__all__ = [
    "DEFAULT_SIGMA_THRESHOLD",
    "DEFAULT_WINDOW_SECONDS",
    "SHADOW_WINDOW_DAYS",
    "AnomalyDetector",
    "AnomalyDetectorConfig",
    "AnomalyObservation",
    "hash_tool_args",
]
