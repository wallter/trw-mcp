"""FR-4 consumer wiring proof (PRD-HPO-PROF-001 seams.wiring_test).

This is the β5 wiring test: it drives the REAL ``trw_session_start`` tool and
proves the resolved hierarchical profile is composed and stamped onto the
session-start payload — i.e. the profile package has a verified consumer in
the same delivery, not a package nothing reads.

The autouse ``_isolate_trw_dir`` conftest fixture redirects
``resolve_trw_dir()`` to ``tmp_path/.trw``, so an ``org.yaml`` written there is
discovered as the org layer by the live resolver.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.conftest import extract_tool_fn, make_test_server


def _session_start_fn() -> Any:
    return extract_tool_fn(make_test_server("ceremony"), "trw_session_start")


def _write_org_layer(tmp_path: Path, body: str) -> None:
    profiles = tmp_path / ".trw" / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    (profiles / "org.yaml").write_text(body, encoding="utf-8")


def test_session_start_returns_resolved_profile(tmp_path: Path) -> None:
    """trw_session_start stamps resolved_profile + snapshot id onto the result.

    This proves the consumer wiring: the profile resolver is actually invoked
    by the live session-start path (FR-4).
    """
    fn = _session_start_fn()
    result: dict[str, Any] = fn(ctx=None, query="*", verbose=True)

    assert "resolved_profile" in result, f"trw_session_start must stamp resolved_profile; keys: {sorted(result)}"
    assert "profile_layers_applied" in result
    assert "profile_snapshot_id" in result
    assert "session_override_hash" in result
    assert result["profile_snapshot_id"].startswith("surf_")
    assert result["session_override_hash"].startswith("sess_")
    # The profile-resolution step is timed in the step-latency telemetry.
    assert "profile_resolve" in result.get("step_durations_ms", {})


def test_session_start_org_layer_flows_into_resolved_profile(tmp_path: Path) -> None:
    """An org.yaml on disk is composed into the live resolved profile (FR-5).

    Drives the full path: disk layer → discover → compose → session payload.
    """
    _write_org_layer(
        tmp_path,
        "rationale: house rules\nreview_threshold: STANDARD\nbuild_check_scope: full\n",
    )
    fn = _session_start_fn()
    result: dict[str, Any] = fn(ctx=None, query="*", verbose=True)

    resolved = result["resolved_profile"]
    assert resolved["review_threshold"] == "STANDARD"
    assert resolved["build_check_scope"] == "full"
    assert "org" in result["profile_layers_applied"]

    # The explanation attributes the value to the org layer (FR-11).
    explanation = result["profile_explanation"]
    review = next(f for f in explanation["fields"] if f["field"] == "review_threshold")
    assert review["origin_layer"] == "org"


def test_session_start_compact_mode_preserves_profile_block(tmp_path: Path) -> None:
    """The compact-by-default payload still carries the resolved profile.

    The trim step only drops diagnostic sub-blocks; the profile block is
    load-bearing and must survive (PRD §9: every run carries the snapshot id).
    """
    fn = _session_start_fn()
    result: dict[str, Any] = fn(ctx=None, query="*", verbose=False)
    assert result.get("compact") is True
    assert "resolved_profile" in result
    assert result["profile_snapshot_id"].startswith("surf_")


def test_session_start_malformed_org_layer_surfaces_structured_error(
    tmp_path: Path,
) -> None:
    """F-02 / FR-12 — a malformed org.yaml surfaces a structured error, not a crash.

    Drives the REAL session start with a schema-invalid persistent layer. The
    resolver fails CLOSED (no silent degrade to defaults) but session start
    still succeeds, surfacing ``profile_resolution_error={path,reason}`` so the
    operator sees which layer is broken.
    """
    # An unknown key fails schema validation in the loader → LayerLoadError.
    _write_org_layer(tmp_path, "totally_unknown_key: 1\n")
    fn = _session_start_fn()
    result: dict[str, Any] = fn(ctx=None, query="*", verbose=True)

    # Session start succeeded despite the broken layer.
    assert "timestamp" in result
    # The structured error is surfaced (not a silent fallback).
    assert "profile_resolution_error" in result, f"expected profile_resolution_error; keys: {sorted(result)}"
    err = result["profile_resolution_error"]
    assert "org.yaml" in err["path"]
    assert err["reason"]
    # Failing closed: no degraded resolved_profile block was emitted.
    assert "resolved_profile" not in result


def test_session_start_profile_disabled_omits_block(tmp_path: Path, monkeypatch: Any) -> None:
    """When profile_system_enabled is False, the block is omitted (fail-open flag).

    Proves the feature flag is honored end-to-end and that its absence does
    not break the rest of session start.
    """
    from trw_mcp.models.config import TRWConfig, get_config

    base = get_config()
    disabled = base.model_copy(update={"profile_system_enabled": False})
    assert isinstance(disabled, TRWConfig)
    monkeypatch.setattr("trw_mcp.tools.ceremony.get_config", lambda: disabled)

    fn = _session_start_fn()
    result: dict[str, Any] = fn(ctx=None, query="*", verbose=True)
    assert "resolved_profile" not in result
    # Session start still succeeds.
    assert "timestamp" in result
