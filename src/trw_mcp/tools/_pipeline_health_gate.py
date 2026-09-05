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
    """Return a reason string when the configured sync targets are misaddressed.

    Empty ``platform_urls`` means sync is simply OFF (not a degradation), so
    neither arm fires without at least one configured target.

    Two signatures, both about WHERE the pipeline's durable copy goes:

    (a) **localhost-only** — every target is loopback. The restored-URL
        regression this gate was built for: nothing leaves the machine.
    (b) **localhost PRIMARY with a remote secondary** — ``platform_urls`` is
        ordered, and slot 0 is the primary (``resolved_sync_targets[0]``).
        Since PRD-FIX-125-FR01 the cycle verdict, both acknowledgement paths and
        ``consecutive_failures`` all follow the PRIMARY, so this ordering points
        every one of them at a dev box and demotes the real backend to a
        best-effort replica that can diverge silently. Before FR01 the ordering
        was nearly harmless because every target had to succeed; afterwards it
        inverts the whole health signal, which is why the arm is added in the
        same change that made the order load-bearing.
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
    if _is_localhost(urls[0]):
        remote = next(u for u in urls[1:] if not _is_localhost(u))
        return (
            f"misconfigured target order: the PRIMARY sync target is local ({urls[0]}) "
            f"while a remote target ({remote}) is configured behind it — the failure counter "
            "and both acknowledgement paths follow platform_urls[0]; put the remote URL first "
            "in .trw/config.yaml"
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

    # An unmeasured probe is not evidence of stale sync (mirrors the identical
    # guard in _check_empty_graph). Without this, a probe that crashed reading
    # an unreadable/corrupt sync-state.json happens to coerce to "no staleness"
    # only because _unmeasured() also zeroes consecutive_failures/last_push_at
    # — an accidental safety, not a declared one. The gate's ``reasons`` list
    # only carries confirmed degradations (no "not measured" slot exists for
    # a single signature), so this stays a silent skip like its sibling rather
    # than inventing a new reason shape here.
    if sync.get("measured") is False:
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
    # An unmeasured probe is not evidence of a dead graph. Without this the
    # fail-closed gate could escalate a store it never managed to read.
    if graph.get("measured") is False:
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
        - ``"healthy"``  — no breakage detected, and everything the gate
          checks was actually measured.
        - ``"degraded"`` — one or more of the three signatures tripped
          (``healthy`` is False; callers fail closed).
        - ``"not_measured"`` — DEF-04: the ``sync_push`` and/or
          ``graph_edges`` probes this gate reuses could not be read (e.g. a
          locked or unreadable database), so neither confirmed signature
          could be evaluated. ``healthy`` stays ``True`` — an unread probe is
          not a CONFIRMED breakage, and this gate's whole design is to never
          wedge ``make check`` on an ambiguous negative (see the module
          docstring) — but the distinct status makes the ambiguity visible in
          ``reasons`` instead of silently reporting the same ``"healthy"``
          verdict a fully-measured, actually-clean run would report.
        - ``"disabled"`` — the kill switch is off (always healthy).
        - ``"probe_error"`` — the gate's own machinery failed; reports healthy
          (fail-open) so a crash cannot wedge CI on a false negative.

    FAILS CLOSED on detected breakage; FAILS OPEN on internal error OR an
    unmeasured probe.
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

    # DEF-04: the two probe-backed checks above silently skip an unmeasured
    # probe (a probe crash is not evidence of the breakage each one detects),
    # which is correct for whether the gate FAILS but wrong for what it
    # REPORTS — a probe nobody could read and a probe that measured clean
    # both landed on the exact same ``{"status": "healthy"}``. Name the gap
    # without blocking on it.
    unmeasured_gate_probes = [
        probe_key
        for probe_key in ("sync_push", "graph_edges")
        if isinstance(health.get(probe_key), dict)
        and health[probe_key].get("measured") is False
        # Mirrors _check_push_staleness's own guard: an unmeasured sync_push
        # probe is only worth reporting when sync is actually configured — a
        # deliberately-off install has nothing to measure in the first place.
        and (probe_key != "sync_push" or _sync_configured(config))
    ]
    if unmeasured_gate_probes:
        reason = f"gate probes not measured: {', '.join(unmeasured_gate_probes)} — could not confirm or clear"
        logger.warning("pipeline_health_gate_not_measured", probes=unmeasured_gate_probes)
        return {"healthy": True, "status": "not_measured", "reasons": [reason]}

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
            "Fix the sync/graph/target breakage. (`pipeline_health_gate_enabled=false` exists "
            "for installs that run TRW with no backend at all — it is not a way to clear a "
            "real breakage, and silencing this gate is how the last one went unseen for 134 days.)",
            file=sys.stderr,
        )
        return 1

    if status == "not_measured":
        # DEF-04: non-blocking by design (see check_pipeline_health's
        # docstring) but printed so an operator sees the ambiguity in a bare
        # ``make check`` log instead of an indistinguishable "OK".
        reasons = [str(r) for r in verdict.get("reasons", [])]
        for reason in reasons:
            print(f"pipeline-health gate: NOT MEASURED (not blocking) — {reason}", file=sys.stderr)

    logger.info("pipeline_health_gate_cli_ok", status=status)
    print(f"pipeline-health gate: OK (status={status})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_gate_cli())
