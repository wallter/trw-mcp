"""FR-4 / FR-13 / NFR-7 — snapshot hashing determinism tests."""

from __future__ import annotations

from trw_mcp.profile import (
    ProfileLayer,
    compute_session_override_hash,
    compute_surface_snapshot_id,
)


def _layer(name: str, **overrides: object) -> ProfileLayer:
    return ProfileLayer(name=name, overrides=overrides)


def test_snapshot_id_cross_platform_canonical() -> None:
    """NFR-7: identical content → identical id regardless of layer order."""
    a = compute_surface_snapshot_id(
        [_layer("defaults", review_threshold="STANDARD"), _layer("org", build_check_scope="full")]
    )
    b = compute_surface_snapshot_id(
        [_layer("org", build_check_scope="full"), _layer("defaults", review_threshold="STANDARD")]
    )
    assert a == b
    assert a.startswith("surf_")


def test_snapshot_excludes_session_layer() -> None:
    """FR-13: the session layer never contributes to surface_snapshot_id."""
    persistent = [_layer("defaults", review_threshold="STANDARD")]
    base = compute_surface_snapshot_id(persistent)
    with_session = compute_surface_snapshot_id([*persistent, _layer("session", cost_budget_usd=5.0)])
    assert base == with_session


def test_session_hash_empty_when_no_session_layer() -> None:
    """FR-13: missing session layer yields the empty-content session hash."""
    no_session = compute_session_override_hash([_layer("defaults", review_threshold="STANDARD")])
    explicit_empty = compute_session_override_hash([_layer("session")])
    assert no_session == explicit_empty
    assert no_session.startswith("sess_")


def test_session_hash_changes_with_session_content() -> None:
    """FR-13: distinct session overrides yield distinct session hashes."""
    one = compute_session_override_hash([_layer("session", cost_budget_usd=1.0)])
    two = compute_session_override_hash([_layer("session", cost_budget_usd=2.0)])
    assert one != two


def test_empty_persistent_layers_hash_stable() -> None:
    """An all-default persistent stack hashes to a stable empty-content id."""
    a = compute_surface_snapshot_id([_layer("defaults")])
    b = compute_surface_snapshot_id([])
    assert a == b


def test_prof001_imports_field_from_meas001_module() -> None:
    """F-09 / FR-8 — the telemetry-carried surface_snapshot_id is the MEAS-001 field.

    FR-8 mandates PROF-001 *reads* the canonical ``surface_snapshot_id`` field
    that PRD-HPO-MEAS-001 defines on ``HPOTelemetryEvent`` rather than declaring
    a competing telemetry field. This proves the canonical field lives in the
    MEAS-001 module (``telemetry/event_base.py``) and that the probe-telemetry
    builder (a PROF-001/CORE-144 consumer) imports the event type from THAT
    module and threads the field through — it does not redefine it.
    """
    from trw_mcp.telemetry.event_base import HPOTelemetryEvent

    # The canonical telemetry field is defined on the MEAS-001 base event.
    assert "surface_snapshot_id" in HPOTelemetryEvent.model_fields

    # The probe telemetry builder imports the event type from the MEAS-001
    # module (not a local shadow) and accepts surface_snapshot_id as a passthrough.
    import inspect

    from trw_mcp.probe import telemetry as probe_telemetry

    assert probe_telemetry.HPOTelemetryEvent is HPOTelemetryEvent
    sig = inspect.signature(probe_telemetry.build_probe_event)
    assert "surface_snapshot_id" in sig.parameters
