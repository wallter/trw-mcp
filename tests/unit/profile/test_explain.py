"""FR-11 — explain payload contract tests."""

from __future__ import annotations

import statistics
import time

from trw_mcp.profile import (
    PROFILE_SURFACE_KEYS,
    ProfileLayer,
    build_explanation,
    compose,
)


def _layer(name: str, **overrides: object) -> ProfileLayer:
    return ProfileLayer(name=name, overrides=overrides)


def test_profile_explain_contract_payload_shape() -> None:
    """FR-11: the payload reports every field's attribution + metadata."""
    resolved = compose(
        [
            _layer("defaults", review_threshold="MINIMAL"),
            _layer("org", review_threshold="STANDARD"),
        ]
    )
    payload = build_explanation(resolved)
    assert set(payload) == {
        "fields",
        "layers_applied",
        "surface_snapshot_id",
        "session_override_hash",
        "resolved_profile",
    }
    # Every surface key appears exactly once in fields.
    field_names = {f["field"] for f in payload["fields"]}  # type: ignore[union-attr]
    assert field_names == set(PROFILE_SURFACE_KEYS)
    for record in payload["fields"]:  # type: ignore[union-attr]
        assert set(record) == {"field", "value", "origin_layer", "override_chain"}


def test_explain_renders_override_chain() -> None:
    """FR-11: the override chain for a contested field shows each contributor."""
    resolved = compose(
        [
            _layer("defaults", review_threshold="MINIMAL"),
            _layer("org", review_threshold="STANDARD"),
        ]
    )
    payload = build_explanation(resolved)
    review = next(
        f
        for f in payload["fields"]
        if f["field"] == "review_threshold"  # type: ignore[union-attr]
    )
    assert review["value"] == "STANDARD"
    assert review["origin_layer"] == "org"
    assert review["override_chain"] == ["defaults:MINIMAL", "org:STANDARD"]


def test_explain_build_latency_under_generous_bound() -> None:
    """F-09 / NFR-5 — building the explanation payload is cheap.

    We assert the MEDIAN over 20 runs (not p95) to avoid flakiness from a
    single GC pause / scheduler hiccup on shared CI hardware, and use a
    deliberately generous 50ms bound — the work is pure in-memory dict
    assembly, so even an order of magnitude of headroom catches an accidental
    O(n^2) / I/O regression without false positives.
    """
    resolved = compose(
        [
            _layer("defaults", review_threshold="MINIMAL", build_check_scope="targeted"),
            _layer("org", review_threshold="STANDARD", cost_budget_usd=10.0),
            _layer("task-type", checkpoint_cadence="aggressive"),
        ]
    )
    samples: list[float] = []
    for _ in range(20):
        start = time.perf_counter()
        build_explanation(resolved)
        samples.append((time.perf_counter() - start) * 1000.0)
    median_ms = statistics.median(samples)
    assert median_ms < 50.0, f"build_explanation median {median_ms:.3f}ms exceeded 50ms bound"
