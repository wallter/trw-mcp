"""Tests for the FR06 pipeline-health ENFORCEMENT gate — PRD-FIX-107 FR06.

The existing ``_pipeline_health.py`` surface is read-only/fail-open ADVISORY.
FR06 ("enforce, don't suggest") turns three breakage signatures into a
fail-CLOSED gate usable by ``make check`` / CI / a deliver-time check:

  (a) push staleness (consecutive_failures over threshold / stale last_push_at)
  (b) knowledge-graph dead (graph_edges == 0 while memories > N)
  (c) misconfigured target (platform_urls contains ONLY localhost when sync
      is configured)

Plus the session_start advisory ESCALATION (prominent, not buried) and a
config KILL SWITCH. These tests are written TDD-first.

Uses tmp_path filesystem fixtures (integration tier).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trw_dir(tmp_path: Path) -> Path:
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    (trw_dir / "memory").mkdir(exist_ok=True)
    (trw_dir / "meta").mkdir(exist_ok=True)
    return trw_dir


def _write_sync_state(trw_dir: Path, state: dict[str, object]) -> None:
    (trw_dir / "sync-state.json").write_text(json.dumps(state))


def _iso_ago(hours: float) -> str:
    return (datetime.now(tz=timezone.utc) - timedelta(hours=hours)).isoformat()


def _make_memory_db(trw_dir: Path, *, corpus: int = 0, edges: int = 0, vec: int | None = None) -> Path:
    db_path = trw_dir / "memory" / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, recall_count INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE IF NOT EXISTS vec_memories (id TEXT PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS memory_graph_edges (id INTEGER PRIMARY KEY, source_id TEXT, target_id TEXT)"
    )
    for i in range(corpus):
        conn.execute("INSERT INTO memories (id, recall_count) VALUES (?, ?)", (f"m{i}", 1))
    # Default to full vec coverage so embedding_coverage stays healthy unless the
    # caller deliberately under-fills it.
    vec_count = corpus if vec is None else vec
    for i in range(vec_count):
        conn.execute("INSERT INTO vec_memories (id) VALUES (?)", (f"m{i}",))
    for i in range(edges):
        conn.execute(
            "INSERT INTO memory_graph_edges (source_id, target_id) VALUES (?, ?)",
            (f"m{i}", f"m{i + 1}"),
        )
    conn.commit()
    conn.close()
    return db_path


def _make_config(*, platform_urls: list[str] | None = None, **overrides: object):
    """Build a minimal TRWConfig for the localhost / kill-switch probes."""
    from trw_mcp.models.config import TRWConfig

    kwargs: dict[str, object] = {}
    if platform_urls is not None:
        kwargs["platform_urls"] = platform_urls
    kwargs.update(overrides)
    return TRWConfig(**kwargs)


def _healthy_pipeline(trw_dir: Path) -> None:
    """Write a fully-healthy pipeline state (no probe degraded)."""
    _write_sync_state(trw_dir, {"consecutive_failures": 0, "last_push_at": _iso_ago(0.5)})
    # Small corpus so graph/recall probes are suppressed (not degraded).
    _make_memory_db(trw_dir, corpus=10, edges=0)


# ---------------------------------------------------------------------------
# (a) Push-staleness condition
# ---------------------------------------------------------------------------


def test_gate_fails_on_push_staleness_consecutive_failures(tmp_path: Path) -> None:
    """High consecutive_failures (with a remote configured) => gate degraded.

    The staleness arm only applies when sync is actually configured (a remote
    ``platform_urls`` entry); see the no-false-positive tests below for the
    sync-off / never-pushed cases.
    """
    from trw_mcp.tools._pipeline_health_gate import check_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 99, "last_push_at": _iso_ago(0.5)})
    _make_memory_db(trw_dir, corpus=10)

    result = check_pipeline_health(trw_dir, _make_config(platform_urls=["https://api.trwframework.com"]))

    assert result["healthy"] is False
    assert result["status"] == "degraded"
    joined = " ".join(result["reasons"]).lower()
    assert "sync" in joined or "push" in joined


def test_gate_fails_on_push_staleness_stale_last_push(tmp_path: Path) -> None:
    """Stale last_push_at (older than the window, remote configured) => gate degraded."""
    from trw_mcp.tools._pipeline_health_gate import check_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 0, "last_push_at": _iso_ago(72)})
    _make_memory_db(trw_dir, corpus=10)

    result = check_pipeline_health(trw_dir, _make_config(platform_urls=["https://api.trwframework.com"]))

    assert result["healthy"] is False
    assert any("sync" in r.lower() or "push" in r.lower() for r in result["reasons"])


def _remote_config(**overrides: object):
    """Config with a real remote sync target — so the push-staleness arm is active."""
    return _make_config(platform_urls=["https://api.trwframework.com"], **overrides)


def test_gate_fails_on_push_staleness_consecutive_failures_with_remote(tmp_path: Path) -> None:
    """High consecutive_failures with a remote configured => degraded."""
    from trw_mcp.tools._pipeline_health_gate import check_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 99, "last_push_at": _iso_ago(0.5)})
    _make_memory_db(trw_dir, corpus=10)

    result = check_pipeline_health(trw_dir, _remote_config())

    assert result["healthy"] is False
    assert any("push" in r.lower() or "sync" in r.lower() for r in result["reasons"])


def test_gate_fails_on_genuinely_stale_push_with_remote(tmp_path: Path) -> None:
    """A real old last_push_at with a remote configured => degraded."""
    from trw_mcp.tools._pipeline_health_gate import check_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 0, "last_push_at": _iso_ago(72)})
    _make_memory_db(trw_dir, corpus=10)

    result = check_pipeline_health(trw_dir, _remote_config())

    assert result["healthy"] is False
    assert any("push" in r.lower() or "sync" in r.lower() for r in result["reasons"])


# ---------------------------------------------------------------------------
# (a') Push-staleness NO-FALSE-POSITIVE rule (PRD-FIX-107 FR06 audit fix)
# ---------------------------------------------------------------------------


def test_gate_healthy_on_fresh_install_no_sync_state_file(tmp_path: Path) -> None:
    """Fresh install: NO sync-state.json at all => push-staleness must NOT trip."""
    from trw_mcp.tools._pipeline_health_gate import check_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    # Deliberately do NOT write sync-state.json.
    _make_memory_db(trw_dir, corpus=10)

    result = check_pipeline_health(trw_dir, _remote_config())

    assert result["healthy"] is True
    assert not any("push" in r.lower() or "sync" in r.lower() for r in result["reasons"])


def test_gate_healthy_when_last_push_at_none(tmp_path: Path) -> None:
    """sync-state.json present but last_push_at is None (never pushed) => not degraded."""
    from trw_mcp.tools._pipeline_health_gate import check_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 0, "last_push_at": None})
    _make_memory_db(trw_dir, corpus=10)

    result = check_pipeline_health(trw_dir, _remote_config())

    assert result["healthy"] is True
    assert not any("push" in r.lower() or "sync" in r.lower() for r in result["reasons"])


def test_gate_healthy_when_sync_off_empty_platform_urls(tmp_path: Path) -> None:
    """Sync OFF (empty platform_urls): even an absent/never-pushed state => not degraded.

    Mirrors the empty-urls guard the localhost check already uses.
    """
    from trw_mcp.tools._pipeline_health_gate import check_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    # No sync-state file + no remote configured = legitimately-off install.
    _make_memory_db(trw_dir, corpus=10)

    result = check_pipeline_health(trw_dir, _make_config(platform_urls=[]))

    assert result["healthy"] is True
    assert not any("push" in r.lower() or "sync" in r.lower() for r in result["reasons"])


def test_gate_healthy_when_sync_off_even_with_stale_state(tmp_path: Path) -> None:
    """Sync OFF: a stale last_push_at left on disk must NOT trip the gate (sync not active)."""
    from trw_mcp.tools._pipeline_health_gate import check_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 99, "last_push_at": _iso_ago(99)})
    _make_memory_db(trw_dir, corpus=10)

    result = check_pipeline_health(trw_dir, _make_config(platform_urls=[]))

    assert result["healthy"] is True
    assert not any("push" in r.lower() or "sync" in r.lower() for r in result["reasons"])


def test_gate_healthy_when_config_none_no_remote(tmp_path: Path) -> None:
    """config=None means no remote known => push-staleness arm stays silent."""
    from trw_mcp.tools._pipeline_health_gate import check_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 99, "last_push_at": _iso_ago(99)})
    _make_memory_db(trw_dir, corpus=10)

    result = check_pipeline_health(trw_dir, None)

    assert result["healthy"] is True
    assert not any("push" in r.lower() or "sync" in r.lower() for r in result["reasons"])


# ---------------------------------------------------------------------------
# (b) Knowledge-graph dead condition
# ---------------------------------------------------------------------------


def test_gate_fails_on_empty_graph(tmp_path: Path) -> None:
    """graph_edges == 0 while memories > N => gate degraded with a graph reason."""
    from trw_mcp.tools._pipeline_health_gate import check_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 0, "last_push_at": _iso_ago(0.5)})
    _make_memory_db(trw_dir, corpus=150, edges=0)

    result = check_pipeline_health(trw_dir, _make_config())

    assert result["healthy"] is False
    assert any("graph" in r.lower() for r in result["reasons"])


def test_gate_silent_on_populated_graph(tmp_path: Path) -> None:
    """A populated graph does NOT trip the graph reason."""
    from trw_mcp.tools._pipeline_health_gate import check_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 0, "last_push_at": _iso_ago(0.5)})
    _make_memory_db(trw_dir, corpus=150, edges=50)

    result = check_pipeline_health(trw_dir, _make_config())

    assert not any("graph" in r.lower() for r in result["reasons"])


# ---------------------------------------------------------------------------
# (c) Misconfigured target (localhost-only) condition
# ---------------------------------------------------------------------------


def test_gate_fails_on_localhost_only_platform_urls(tmp_path: Path) -> None:
    """platform_urls contains ONLY localhost => gate degraded with a target reason."""
    from trw_mcp.tools._pipeline_health_gate import check_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _healthy_pipeline(trw_dir)
    config = _make_config(platform_urls=["http://127.0.0.1:5002", "http://localhost:9000"])

    result = check_pipeline_health(trw_dir, config)

    assert result["healthy"] is False
    assert any("localhost" in r.lower() or "platform_urls" in r.lower() for r in result["reasons"])


def test_gate_silent_with_remote_platform_url(tmp_path: Path) -> None:
    """A remote URL present (even alongside localhost) => no target reason."""
    from trw_mcp.tools._pipeline_health_gate import check_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _healthy_pipeline(trw_dir)
    config = _make_config(platform_urls=["http://localhost:5002", "https://api.trwframework.com"])

    result = check_pipeline_health(trw_dir, config)

    assert not any("localhost" in r.lower() or "platform_urls" in r.lower() for r in result["reasons"])


def test_gate_silent_when_no_platform_urls_configured(tmp_path: Path) -> None:
    """No platform_urls at all (sync not configured) => localhost rule does NOT fire.

    The localhost-only signature is the *restored-URL regression* — it only
    applies when sync targets ARE configured but all point at localhost. An
    empty config means sync is simply off, which is not a degradation.
    """
    from trw_mcp.tools._pipeline_health_gate import check_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _healthy_pipeline(trw_dir)
    config = _make_config(platform_urls=[])

    result = check_pipeline_health(trw_dir, config)

    assert not any("localhost" in r.lower() or "platform_urls" in r.lower() for r in result["reasons"])


# ---------------------------------------------------------------------------
# Healthy pipeline passes
# ---------------------------------------------------------------------------


def test_gate_passes_when_healthy(tmp_path: Path) -> None:
    """All three signatures clear => healthy, status='healthy', no reasons."""
    from trw_mcp.tools._pipeline_health_gate import check_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    _healthy_pipeline(trw_dir)
    config = _make_config(platform_urls=["https://api.trwframework.com"])

    result = check_pipeline_health(trw_dir, config)

    assert result["healthy"] is True
    assert result["status"] == "healthy"
    assert result["reasons"] == []


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------


def test_kill_switch_disables_gate(tmp_path: Path) -> None:
    """pipeline_health_gate_enabled=False => always healthy, even when broken."""
    from trw_mcp.tools._pipeline_health_gate import check_pipeline_health

    trw_dir = _make_trw_dir(tmp_path)
    # Maximally broken: stale push, empty graph, localhost-only.
    _write_sync_state(trw_dir, {"consecutive_failures": 99, "last_push_at": _iso_ago(99)})
    _make_memory_db(trw_dir, corpus=200, edges=0)
    config = _make_config(
        platform_urls=["http://127.0.0.1:5002"],
        pipeline_health_gate_enabled=False,
    )

    result = check_pipeline_health(trw_dir, config)

    assert result["healthy"] is True
    assert result["status"] == "disabled"
    assert result["reasons"] == []


def test_gate_enabled_by_default(tmp_path: Path) -> None:
    """The gate config field defaults to enabled (enforce, don't suggest)."""
    config = _make_config()
    assert config.pipeline_health_gate_enabled is True


# ---------------------------------------------------------------------------
# Fail-open robustness — the gate function itself must never raise
# ---------------------------------------------------------------------------


def test_gate_fail_open_on_internal_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If the underlying probe aggregator explodes, the gate reports healthy (fail-open).

    The gate FAILS CLOSED on detected breakage but must FAIL OPEN on its own
    internal error — a probe crash must not wedge CI on a false negative.
    """
    import trw_mcp.tools._pipeline_health_gate as gate_mod

    def _boom(_trw_dir: Path) -> dict[str, object]:
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(gate_mod, "step_pipeline_health", _boom)

    trw_dir = _make_trw_dir(tmp_path)
    result = gate_mod.check_pipeline_health(trw_dir, _make_config())

    assert result["healthy"] is True
    assert result["status"] == "probe_error"


# ---------------------------------------------------------------------------
# Session-start advisory ESCALATION
# ---------------------------------------------------------------------------


def test_session_start_escalates_to_prominent_warning_when_gate_trips(tmp_path: Path) -> None:
    """When the gate trips, session_start surfaces a PROMINENT (escalated) warning,
    not merely the buried compact advisory string."""
    from trw_mcp.tools._ceremony_session_start_steps import step_pipeline_health_advisory

    trw_dir = _make_trw_dir(tmp_path)
    # Empty graph with a large corpus => gate trips.
    _write_sync_state(trw_dir, {"consecutive_failures": 0, "last_push_at": _iso_ago(0.5)})
    _make_memory_db(trw_dir, corpus=150, edges=0)

    results: dict[str, object] = {}
    config = _make_config(platform_urls=["https://api.trwframework.com"])
    step_pipeline_health_advisory(trw_dir, results, config)

    assert "pipeline_health_advisory" in results
    # Escalated: a structured warning surface, prominently flagged.
    warning = results.get("pipeline_health_warning")
    assert isinstance(warning, dict)
    assert warning.get("enforce") is True
    assert warning.get("severity") in {"error", "critical", "warning"}
    assert isinstance(warning.get("reasons"), list) and warning["reasons"]


def test_session_start_silent_when_healthy(tmp_path: Path) -> None:
    """A healthy pipeline injects neither the advisory nor the escalated warning."""
    from trw_mcp.tools._ceremony_session_start_steps import step_pipeline_health_advisory

    trw_dir = _make_trw_dir(tmp_path)
    _healthy_pipeline(trw_dir)

    results: dict[str, object] = {}
    config = _make_config(platform_urls=["https://api.trwframework.com"])
    step_pipeline_health_advisory(trw_dir, results, config)

    assert "pipeline_health_advisory" not in results
    assert "pipeline_health_warning" not in results


def test_session_start_advisory_accepts_no_config(tmp_path: Path) -> None:
    """Back-compat: step_pipeline_health_advisory still works when config is omitted."""
    from trw_mcp.tools._ceremony_session_start_steps import step_pipeline_health_advisory

    trw_dir = _make_trw_dir(tmp_path)
    _write_sync_state(trw_dir, {"consecutive_failures": 0, "last_push_at": _iso_ago(0.5)})
    _make_memory_db(trw_dir, corpus=150, edges=0)

    results: dict[str, object] = {}
    # No config argument — must not raise.
    step_pipeline_health_advisory(trw_dir, results)

    assert "pipeline_health_advisory" in results


# ---------------------------------------------------------------------------
# Fail-CLOSED enforcement CLI (PRD-FIX-107 FR06 — "enforce, don't suggest")
# ---------------------------------------------------------------------------


def _stub_cli_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, verdict: dict[str, object]):
    """Patch the CLI's trw_dir/config resolution + gate verdict so no live state is touched.

    ``run_gate_cli`` does local imports of ``resolve_trw_dir`` / ``get_config``, so we
    patch them at their SOURCE modules (not on the gate module).
    """
    import trw_mcp.models.config._loader as loader_mod
    import trw_mcp.state._paths as paths_mod
    import trw_mcp.tools._pipeline_health_gate as gate_mod

    trw_dir = _make_trw_dir(tmp_path)
    monkeypatch.setattr(paths_mod, "resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr(loader_mod, "get_config", lambda: _make_config())
    monkeypatch.setattr(gate_mod, "check_pipeline_health", lambda _trw_dir, _config: verdict)
    return gate_mod


def test_cli_exits_1_when_degraded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An unhealthy/degraded verdict => CLI exit code 1 (the enforcement signal)."""
    gate_mod = _stub_cli_env(
        monkeypatch,
        tmp_path,
        {
            "healthy": False,
            "status": "degraded",
            "reasons": ["push staleness: 99 consecutive failures"],
        },
    )
    assert gate_mod.run_gate_cli() == 1


def test_cli_exits_0_when_healthy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A healthy verdict => CLI exit code 0."""
    gate_mod = _stub_cli_env(monkeypatch, tmp_path, {"healthy": True, "status": "healthy", "reasons": []})
    assert gate_mod.run_gate_cli() == 0


def test_cli_exits_0_on_probe_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """probe_error (fail-open) => CLI exit code 0; a crash must never wedge CI."""
    gate_mod = _stub_cli_env(monkeypatch, tmp_path, {"healthy": True, "status": "probe_error", "reasons": []})
    assert gate_mod.run_gate_cli() == 0


def test_cli_exits_0_on_disabled_kill_switch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Kill switch off (status='disabled', healthy True) => CLI exit code 0."""
    gate_mod = _stub_cli_env(monkeypatch, tmp_path, {"healthy": True, "status": "disabled", "reasons": []})
    assert gate_mod.run_gate_cli() == 0


def test_cli_fail_open_on_setup_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If the gate / resolution itself raises, the CLI fails OPEN (exit 0)."""
    import trw_mcp.models.config._loader as loader_mod
    import trw_mcp.state._paths as paths_mod
    import trw_mcp.tools._pipeline_health_gate as gate_mod

    trw_dir = _make_trw_dir(tmp_path)
    monkeypatch.setattr(paths_mod, "resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr(loader_mod, "get_config", lambda: _make_config())

    def _boom(_trw_dir: object, _config: object) -> dict[str, object]:
        raise RuntimeError("setup exploded")

    monkeypatch.setattr(gate_mod, "check_pipeline_health", _boom)

    assert gate_mod.run_gate_cli() == 0
