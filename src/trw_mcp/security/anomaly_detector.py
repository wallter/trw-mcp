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
from collections import OrderedDict, defaultdict, deque
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field

from trw_mcp._checkout_write import UnsafeWriteError, append_checkout_file, write_checkout_file
from trw_mcp.security._anomaly_state import SHADOW_WINDOW_DAYS as SHADOW_WINDOW_DAYS
from trw_mcp.security._anomaly_state import (
    _ensure_shadow_clock,
    _state_unwritable,
    read_state_file,
    state_persistable,
)
from trw_mcp.telemetry.event_base import MCPSecurityEvent
from trw_mcp.telemetry.unified_events import emit as emit_unified_event

logger = structlog.get_logger(__name__)

DEFAULT_SIGMA_THRESHOLD = 5.0
DEFAULT_WINDOW_SECONDS = 60
# Per-(server, tool) cap on remembered novel arg-hashes. Bounds in-memory
# growth and (via the persisted store) on-disk growth so an attacker cannot
# DoS the detector by flooding novel args. Mirrors the maxlen=32 discipline
# already applied to ``_baseline_rates``.
DEFAULT_MAX_ARG_HASHES_PER_PAIR = 1024
# Roll the baseline store file once it exceeds this many lines, keeping the
# most recent tail. Prevents unbounded disk growth from the append-only log.
DEFAULT_MAX_BASELINE_STORE_LINES = 100_000
# W41-6 re-baseline (measured against a replay of `.trw/context/tool_call_events.jsonl`,
# 2026-09-24): a full-content arg-hash has no ceiling for a free-text-bearing tool
# (trw_assess/trw_learn/trw_checkpoint state/summary text is ~never byte-identical twice),
# so "fire on every hash not seen before" fired on 44.9-46.0% of calls to those tools and
# 37.7% of that day's traffic overall -- 456 of 457 anomalies emitted that day were
# novel_arg_pattern, and rate_spike/namespace_mismatch fired zero times across all 9 days
# of replayed traffic (2026-09-16 to 2026-09-24), so nothing with genuine signal was lost.
# Capping emission to the first N distinct argument shapes ever observed per (server, tool)
# pair -- reusing the ALREADY-persisted `_baseline_arg_hashes` count, no new state -- drops
# the simulated 2026-09-24 replay rate to 0.90% (11 of 1227 calls) while still catching a
# genuinely new SHAPE during the tool's early life, which is what FR-4's threat model
# (tool-squatting / argument-swapping) actually needs.
DEFAULT_MAX_NOVEL_ARG_SHAPES_PER_PAIR = 25


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
    #: The call's arguments are free text by design (see :func:`is_novelty_exempt`): no shape worth a signal.
    novelty_exempt: bool = False


class AnomalyDetectorConfig(BaseModel):
    """Shadow-mode detector configuration (PRD §13.4)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    mode: Literal["shadow", "enforce"] = "shadow"
    sigma_threshold: float = Field(default=DEFAULT_SIGMA_THRESHOLD, gt=0.0)
    window_seconds: int = Field(default=DEFAULT_WINDOW_SECONDS, gt=0)
    #: The project root every state read and write stays beneath (CORE-337-D): a symlinked component under it is
    #: refused. ``None`` keeps the state in memory only.
    checkout_root: Path | None = None
    shadow_clock_path: Path
    baseline_store_path: Path | None = None
    #: False for a stateless reviewer session (CODEX-P0-B-REVIEWER-WRITES): the shadow clock and the
    #: argument-hash baseline are then never created or appended in the repository under review.
    persist_state: bool = True
    max_arg_hashes_per_pair: int = Field(default=DEFAULT_MAX_ARG_HASHES_PER_PAIR, gt=0)
    max_baseline_store_lines: int = Field(default=DEFAULT_MAX_BASELINE_STORE_LINES, gt=0)
    max_novel_arg_shapes_per_pair: int = Field(default=DEFAULT_MAX_NOVEL_ARG_SHAPES_PER_PAIR, gt=0)


def _hash_args(args: dict[str, Any]) -> str:
    """Stable SHA-256 over canonicalized JSON of the args dict (FR-4)."""
    blob = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def is_novelty_exempt(server: str, tool: str, args: dict[str, Any], *, own_server: str = "trw") -> bool:
    """True only for TRW's own ``trw_status(feedback=...)``: its argument is free text, so every call is a novel hash.

    Bound to the RESOLVED server and the exact tool name (feedback #141): a foreign server that names a tool
    ``trw_status`` stays monitored.
    """
    return server == own_server and tool == "trw_status" and bool(args.get("feedback"))


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
        # Bounded LRU set per (server, tool): an OrderedDict keyed by arg-hash
        # acts as an insertion-ordered set; oldest entries are evicted once the
        # per-pair cap is reached so novel-arg flooding cannot grow memory
        # without bound.
        self._baseline_arg_hashes: dict[tuple[str, str], OrderedDict[str, None]] = defaultdict(OrderedDict)
        # PRD-INFRA-SEC-001 FR-3 NFR: the shadow clock is written on the FIRST
        # OBSERVATION (see ``observe``), not on construction. The detector is
        # built as part of ``create_app()`` (which used to run at import time, so
        # writing here made merely importing ``trw_mcp.server`` create
        # ``.trw/security/mcp_shadow_start.yaml`` in the caller's cwd); building
        # an app is still not a tool call.
        self._shadow_clock_ensured = False
        # CORE-337-D: state is read and written only beneath a project root, descriptor-anchored and no-follow.
        self._persist = state_persistable(config.checkout_root, config.persist_state)
        if config.persist_state and not self._persist:
            logger.warning("mcp_anomaly_state_in_memory", reason="no checkout root or no no-follow file support")
        self._load_arg_hash_baseline()

    def _remember_arg_hash(self, key: tuple[str, str], args_hash: str) -> None:
        """Record ``args_hash`` for ``key``, evicting the oldest beyond the cap."""
        bucket = self._baseline_arg_hashes[key]
        bucket.pop(args_hash, None)
        bucket[args_hash] = None
        cap = self._config.max_arg_hashes_per_pair
        while len(bucket) > cap:
            bucket.popitem(last=False)

    @property
    def mode(self) -> Literal["shadow", "enforce"]:
        """Detector operating mode (``shadow`` = observe-only, ``enforce`` = blocks).

        Public accessor so callers (e.g. the security middleware) do not reach
        into the private ``_config`` to decide whether to act on a fired anomaly.
        """
        return self._config.mode

    def _load_arg_hash_baseline(self) -> None:
        path = self._config.baseline_store_path
        root = self._config.checkout_root
        if path is None or root is None or not self._persist:
            return
        try:
            text = read_state_file(root, path)
        except (
            OSError,
            ValueError,
            UnsafeWriteError,
        ):  # justified: boundary, a linked or unreadable store is skipped, never followed
            logger.warning("mcp_arg_baseline_load_failed", path=str(path), outcome="skipped")
            return
        lines = (text or "").splitlines()
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
                key = (server, tool)
                self._baseline_pairs.add(key)
                self._remember_arg_hash(key, args_hash)

    def _persist_arg_hash_baseline(self, obs: AnomalyObservation) -> None:
        path = self._config.baseline_store_path
        if path is None or not obs.args_hash or not self._persist:
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
        try:
            # Through the checkout adapter (CORE-337-D): a planted symlink at the store or any directory above it is
            # refused, never appended through; the adapter creates missing directories without following a link.
            append_checkout_file(self._state_root(), path, json.dumps(payload, sort_keys=True) + "\n")
        except (
            OSError,
            UnsafeWriteError,
        ) as exc:  # trw-fail-silent-allow: a read-only sandbox or a planted symlink must not fail the tool call; logged, baseline stays in memory
            _state_unwritable(path, exc)
            return
        self._roll_baseline_store(path)

    def _state_root(self) -> Path:
        """The project root; set whenever ``self._persist`` is (``state_persistable``)."""
        root = self._config.checkout_root
        if root is None:  # unreachable while self._persist holds; a None here would write beside the file instead
            raise RuntimeError("anomaly detector state has no checkout root")
        return root

    def _roll_baseline_store(self, path: Path) -> None:
        """Truncate the append-only baseline store to its most recent tail.

        Without this the file grows without bound (novel-arg flooding DoS).
        Keeps the last ``max_baseline_store_lines`` lines.
        """
        cap = self._config.max_baseline_store_lines
        try:
            lines = (read_state_file(self._state_root(), path) or "").splitlines()
        except (
            OSError,
            ValueError,
            UnsafeWriteError,
        ):
            return  # trw-fail-silent-allow: skip the roll rather than fail the tool call; a linked store is never read
        if len(lines) <= cap:
            return
        tail = lines[-cap:]
        try:
            # An atomic replace beneath the root with an unpredictable temp name (CORE-337-D: a fixed <name>.tmp could
            # be planted as a symlink and written through).
            write_checkout_file(self._state_root(), path, "\n".join(tail) + "\n")
        except (OSError, UnsafeWriteError):  # justified: boundary, leave original file intact on roll failure
            logger.warning("mcp_arg_baseline_roll_failed", path=str(path), outcome="skipped")
            return
        logger.info(
            "mcp_arg_baseline_rolled",
            path=str(path),
            kept_lines=len(tail),
            outcome="truncated",
        )

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
                for arg_hash in arg_hashes:
                    self._remember_arg_hash(pair, str(arg_hash))

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
        if not self._shadow_clock_ensured and self._persist:
            try:
                _ensure_shadow_clock(self._config.shadow_clock_path, root=self._state_root(), now=self._now_fn())
                self._shadow_clock_ensured = True
            except OSError as exc:  # trw-fail-silent-allow: a read-only sandbox must not fail the tool call; logged, retried next call
                _state_unwritable(self._config.shadow_clock_path, exc)
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
            # Record the pair before emitting so subsequent calls skip it.
            # Without this the anomaly fires on every single tool invocation
            # for the life of the process, since seed_baseline is only ever
            # called by tests / Phase-2 calibration paths.
            self._baseline_pairs.add((obs.server, obs.tool))
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
        if (
            obs.args_hash
            and not obs.novelty_exempt
            and obs.args_hash not in self._baseline_arg_hashes[(obs.server, obs.tool)]
        ):
            key = (obs.server, obs.tool)
            # W41-6: only the first `max_novel_arg_shapes_per_pair` distinct argument shapes
            # EVER observed for this pair are worth a signal -- past that, on a free-text tool
            # every call is a "novel" hash forever, and the emission degenerates into pure
            # per-call noise (measured 44.9-46.0% of trw_assess/trw_learn/trw_checkpoint calls
            # flagged with zero true positives). `len(...)` reuses the already-persisted,
            # already-bounded arg-hash baseline as the shape count -- no new persisted state.
            prior_shape_count = len(self._baseline_arg_hashes[key])
            self._remember_arg_hash(key, obs.args_hash)
            self._persist_arg_hash_baseline(obs)
            if prior_shape_count < self._config.max_novel_arg_shapes_per_pair:
                _emit_anomaly(
                    anomaly_type="novel_arg_pattern",
                    server=obs.server,
                    tool=obs.tool,
                    session_id=obs.session_id,
                    run_id=obs.run_id,
                    run_dir=self._run_dir,
                    fallback_dir=self._fallback_dir,
                    extra={
                        "args_hash": obs.args_hash,
                        "novel_arg_pattern": True,
                        "arg_shape_count": prior_shape_count + 1,
                    },
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
    "is_novelty_exempt",
]
