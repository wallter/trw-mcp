"""Fail-closed pipeline-health ENFORCEMENT gate — PRD-FIX-107 FR06.

The ``_pipeline_health.py`` surface is read-only/fail-open ADVISORY. This
module turns three breakage signatures into an enforcement gate
("enforce, don't suggest") usable by ``make check`` / CI / a deliver-time
check so a silent compounding-pipeline outage can never recur:

  (a) push staleness  — ``sync-state.json`` consecutive_failures over a
      configurable threshold OR last_push_at stale beyond a window.
  (b) knowledge-graph dead — ``graph_edges == 0`` while ``memories > N``.
  (c) misconfigured target — ``platform_urls`` contains ONLY localhost
      entries when sync targets ARE configured (the restored-URL regression).

Design:
- Reuses the existing read-only probes via ``step_pipeline_health`` for (a)/(b)
  (no duplicated DB/sync-state I/O — DRY).
- (c) is config-derived (the read-only DB probes never see config), so it lives
  here against ``TRWConfig.platform_urls``.
- ``check_pipeline_health`` FAILS CLOSED on any detected breakage but FAILS
  OPEN on its own internal error — a probe crash must never wedge CI on a
  false negative.
- A config kill switch (``pipeline_health_gate_enabled``) and configurable
  thresholds gate the whole surface.

Kept in its own module so ``_pipeline_health.py`` stays under the 350-LOC gate.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from trw_mcp.tools._pipeline_health import step_pipeline_health

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)

GateResult = dict[str, Any]

# Hosts treated as non-remote for the localhost-only signature. ``0.0.0.0`` is
# detected as a misconfiguration target here; this module never binds sockets.
_LOCALHOST_HOSTS: tuple[str, ...] = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]")  # noqa: S104


def _is_localhost(url: str) -> bool:
    """True when *url*'s host is a loopback / non-routable local address."""
    lowered = url.strip().lower()
    # Strip scheme if present so bare "localhost:5002" also matches.
    if "://" in lowered:
        lowered = lowered.split("://", 1)[1]
    host = lowered.split("/", 1)[0]
    # Drop any userinfo and keep host[:port].
    host = host.rsplit("@", 1)[-1]
    return any(host == loop or host.startswith(loop + ":") for loop in _LOCALHOST_HOSTS)


def _check_localhost_only(config: TRWConfig | None) -> str | None:
    """Return a reason string when sync is configured but ALL targets are localhost.

    Empty ``platform_urls`` means sync is simply OFF (not a degradation), so the
    rule only fires when one-or-more targets exist and EVERY one is localhost.
    """
    if config is None:
        return None
    urls = [u for u in getattr(config, "platform_urls", []) if isinstance(u, str) and u.strip()]
    if not urls:
        return None
    if all(_is_localhost(u) for u in urls):
        return (
            f"misconfigured target: platform_urls is localhost-only ({', '.join(urls)}) "
            "— restore the remote sync URL in .trw/config.yaml"
        )
    return None


def _sync_configured(config: TRWConfig | None) -> bool:
    """True when at least one non-empty sync target URL is configured.

    Mirrors the empty-``platform_urls`` guard in :func:`_check_localhost_only`:
    an empty list means sync is simply OFF, which is never a degradation.
    """
    if config is None:
        return False
    urls = [u for u in getattr(config, "platform_urls", []) if isinstance(u, str) and u.strip()]
    return bool(urls)


def _check_push_staleness(health: GateResult, config: TRWConfig | None) -> str | None:
    """Return a reason string when sync push is stale per the gate thresholds.

    Reuses the read-only ``sync_push`` probe result but re-evaluates against the
    gate-specific thresholds so the enforcement window is independently tunable.

    No-false-positive rule (PRD-FIX-107 FR06): the staleness signature only
    fires when sync is actually configured AND has actually pushed before.
    Specifically it does NOT trip when:
      - ``platform_urls`` is empty (sync OFF — mirrors the localhost guard), or
      - ``sync-state.json`` is absent / ``last_push_at`` is None (fresh install
        or sync legitimately never pushed — ``last_push_at is None``).
    Only a real stale ``last_push_at`` older than the window, or a high
    ``consecutive_failures`` count with sync configured, trips the gate.
    """
    sync = health.get("sync_push")
    if not isinstance(sync, dict):
        return None

    # Sync OFF (no remote configured) is not a degradation — same guard the
    # localhost-only signature uses.
    if not _sync_configured(config):
        return None

    failure_threshold = 10
    stale_hours = 6.0
    if config is not None:
        failure_threshold = int(getattr(config, "pipeline_health_gate_failure_threshold", 10))
        stale_hours = float(getattr(config, "pipeline_health_gate_stale_hours", 6.0))

    failures_raw = sync.get("consecutive_failures", 0)
    consecutive_failures = int(failures_raw) if isinstance(failures_raw, (int, float)) else 0

    last_push_raw = sync.get("last_push_at")
    last_push_at = last_push_raw if isinstance(last_push_raw, str) and last_push_raw else None

    age_hours: float | None = None
    if last_push_at is not None:
        try:
            dt = datetime.fromisoformat(last_push_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(tz=timezone.utc) - dt).total_seconds() / 3600.0
        except ValueError:
            age_hours = None

    failure_degraded = consecutive_failures >= failure_threshold
    # Never-pushed (last_push_at / age_hours is None) is NOT stale — only an
    # actual last_push_at older than the window trips the staleness arm.
    stale_degraded = age_hours is not None and age_hours > stale_hours
    if not (failure_degraded or stale_degraded):
        return None

    push_desc = "never" if last_push_at is None else last_push_at
    return (
        f"push staleness: {consecutive_failures} consecutive failures "
        f"(threshold {failure_threshold}); last push {push_desc}"
    )


def _check_empty_graph(health: GateResult, config: TRWConfig | None) -> str | None:
    """Return a reason string when the knowledge graph is empty for a populated corpus."""
    graph = health.get("graph_edges")
    if not isinstance(graph, dict):
        return None

    min_corpus = 10
    if config is not None:
        min_corpus = int(getattr(config, "pipeline_health_gate_graph_min_corpus", 10))

    edge_raw = graph.get("edge_count", 0)
    edge_count = int(edge_raw) if isinstance(edge_raw, (int, float)) else 0
    corpus_raw = graph.get("corpus_count", 0)
    corpus_count = int(corpus_raw) if isinstance(corpus_raw, (int, float)) else 0

    if edge_count == 0 and corpus_count > min_corpus:
        return f"knowledge graph dead: 0 edges for {corpus_count} memories (min corpus {min_corpus})"
    return None


def check_pipeline_health(trw_dir: Path, config: TRWConfig | None = None) -> GateResult:
    """Fail-closed pipeline-health gate (PRD-FIX-107 FR06).

    Returns a structured verdict:
        ``{"healthy": bool, "status": str, "reasons": list[str]}``

    ``status`` is one of:
        - ``"healthy"``  — no breakage detected.
        - ``"degraded"`` — one or more of the three signatures tripped
          (``healthy`` is False; callers fail closed).
        - ``"disabled"`` — the kill switch is off (always healthy).
        - ``"probe_error"`` — the gate's own machinery failed; reports healthy
          (fail-open) so a crash cannot wedge CI on a false negative.

    FAILS CLOSED on detected breakage; FAILS OPEN on internal error.
    """
    if config is not None and not bool(getattr(config, "pipeline_health_gate_enabled", True)):
        return {"healthy": True, "status": "disabled", "reasons": []}

    try:
        health = step_pipeline_health(trw_dir)
    except Exception as exc:  # justified: fail-open on internal error, never wedge CI on a false negative
        logger.warning("pipeline_health_gate_probe_failed", error=str(exc))
        return {"healthy": True, "status": "probe_error", "reasons": []}

    reasons = [
        reason
        for reason in (
            _check_push_staleness(health, config),
            _check_empty_graph(health, config),
            _check_localhost_only(config),
        )
        if reason
    ]

    if reasons:
        logger.error(
            "pipeline_health_gate_failed",
            reasons=reasons,
            count=len(reasons),
        )
        return {"healthy": False, "status": "degraded", "reasons": reasons}

    return {"healthy": True, "status": "healthy", "reasons": []}


def run_gate_cli() -> int:
    """Fail-CLOSED CLI entry for the FR06 pipeline-health gate ("enforce, don't suggest").

    Resolves the live ``.trw`` dir + ``TRWConfig`` and runs
    :func:`check_pipeline_health`. Returns a process exit code:

      - ``1`` ONLY when the gate is genuinely ``degraded`` (status="degraded",
        ``healthy is False``) — the enforcement signal.
      - ``0`` when healthy, disabled (kill switch off), or ``probe_error``
        (fail-open: a probe crash must never wedge CI on a false negative).

    Reasons are emitted via structlog AND printed to stderr so the failure is
    visible in a bare ``make``/CI log without structured-log plumbing.
    """
    try:
        from trw_mcp.models.config._loader import get_config
        from trw_mcp.state._paths import resolve_trw_dir

        trw_dir = resolve_trw_dir()
        config = get_config()
        verdict = check_pipeline_health(trw_dir, config)
    except Exception as exc:  # justified: fail-open — never wedge CI on the gate's own error
        logger.warning("pipeline_health_gate_cli_error", error=str(exc))
        print(f"pipeline-health gate: probe/setup error ({exc}) — passing (fail-open)", file=sys.stderr)
        return 0

    status = str(verdict.get("status", ""))
    if verdict.get("healthy") is False and status == "degraded":
        reasons = [str(r) for r in verdict.get("reasons", [])]
        logger.error("pipeline_health_gate_cli_failed", reasons=reasons, count=len(reasons))
        print("pipeline-health gate: FAILED (compounding pipeline degraded):", file=sys.stderr)
        for reason in reasons:
            print(f"  - {reason}", file=sys.stderr)
        print(
            "Fix the sync/graph/target breakage, or set pipeline_health_gate_enabled=false "
            "(TRW_PIPELINE_HEALTH_GATE_ENABLED=0) once activation is complete.",
            file=sys.stderr,
        )
        return 1

    logger.info("pipeline_health_gate_cli_ok", status=status)
    print(f"pipeline-health gate: OK (status={status})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_gate_cli())
