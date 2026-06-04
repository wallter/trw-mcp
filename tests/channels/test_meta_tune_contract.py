"""Tests for cross-client meta-tune contract constants (PRD-DIST-2400 FR22-FR24).

Covers JOIN_KEY_FIELDS, DEFAULT_CORRELATION_WINDOW_SECONDS,
CLIENT_CORRECTION_FACTORS, CLIENT_THROTTLE_THRESHOLDS, and
COPILOT_THROTTLE_MIN_N / DEFAULT_THROTTLE_MIN_N.
"""

from __future__ import annotations

import pytest

from trw_mcp.channels._manifest_models import (
    CLIENT_CORRECTION_FACTORS,
    CLIENT_THROTTLE_THRESHOLDS,
    COPILOT_THROTTLE_MIN_N,
    DEFAULT_CORRELATION_WINDOW_SECONDS,
    DEFAULT_THROTTLE_MIN_N,
    JOIN_KEY_FIELDS,
)
from trw_mcp.channels.meta_tune._correlator import adjusted_rate

# ---------------------------------------------------------------------------
# FR22 — join key fields + correlation window
# ---------------------------------------------------------------------------


def test_join_key_fields_type() -> None:
    """JOIN_KEY_FIELDS must be a tuple[str, str]."""
    assert isinstance(JOIN_KEY_FIELDS, tuple)
    assert len(JOIN_KEY_FIELDS) == 2
    assert all(isinstance(k, str) for k in JOIN_KEY_FIELDS)


def test_join_key_fields_values() -> None:
    """JOIN_KEY_FIELDS must be exactly ('session_id', 'file_path')."""
    assert JOIN_KEY_FIELDS == ("session_id", "file_path")


def test_default_correlation_window() -> None:
    """DEFAULT_CORRELATION_WINDOW_SECONDS must be 3600 (one hour)."""
    assert DEFAULT_CORRELATION_WINDOW_SECONDS == 3600


# ---------------------------------------------------------------------------
# FR23 — client correction factors
# ---------------------------------------------------------------------------


_EXPECTED_CORRECTION_FACTORS = {
    "claude-code": 0.85,
    "codex": 0.70,
    "antigravity-cli": 0.50,
    "opencode": 0.40,
    "cursor-ide": 0.75,
    "cursor-cli": 0.75,
    "copilot": 0.50,
}


def test_correction_factor_clients_present() -> None:
    """All 7 clients must have a correction factor defined."""
    for client in _EXPECTED_CORRECTION_FACTORS:
        assert client in CLIENT_CORRECTION_FACTORS, f"Missing correction factor for {client!r}"


@pytest.mark.parametrize("client,expected", list(_EXPECTED_CORRECTION_FACTORS.items()))
def test_correction_factor_values(client: str, expected: float) -> None:
    """Each client's correction factor must match master plan §7.3."""
    assert CLIENT_CORRECTION_FACTORS[client] == pytest.approx(expected, abs=1e-9)


def test_correction_factor_adjusted_rate_capped_at_1() -> None:
    """adjusted_rate = min(raw / factor, 1.0) — must never exceed 1.0."""
    # With raw_rate=1.0 and factor=0.4 (opencode), raw/factor = 2.5 — capped to 1.0
    result = adjusted_rate(1.0, "opencode")
    assert result == pytest.approx(1.0)


def test_correction_factor_adjusted_rate_scales_up() -> None:
    """adjusted_rate scales raw_rate up when factor < 1.0."""
    # claude-code factor=0.85: raw=0.425 → adj=0.5
    result = adjusted_rate(0.425, "claude-code")
    assert result == pytest.approx(0.5, abs=1e-6)


def test_correction_factor_unknown_client_defaults_to_1() -> None:
    """Unknown clients use factor=1.0 (no adjustment)."""
    result = adjusted_rate(0.5, "unknown-client")
    assert result == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# FR24 — per-client throttle thresholds
# ---------------------------------------------------------------------------


_EXPECTED_THROTTLE_THRESHOLDS = {
    "claude-code": (0.25, 3),
    "codex": (0.20, 3),
    "antigravity-cli": (0.15, 5),
    "opencode": (0.15, 5),
    "cursor-ide": (0.20, 3),
    "cursor-cli": (0.20, 3),
    "copilot": (0.15, 5),
}


def test_throttle_threshold_clients_present() -> None:
    """All 7 clients must have a throttle threshold defined."""
    for client in _EXPECTED_THROTTLE_THRESHOLDS:
        assert client in CLIENT_THROTTLE_THRESHOLDS, f"Missing threshold for {client!r}"


@pytest.mark.parametrize("client,expected", list(_EXPECTED_THROTTLE_THRESHOLDS.items()))
def test_throttle_threshold_values(client: str, expected: tuple[float, int]) -> None:
    """Each client's (threshold, window_count) must match master plan §7.4."""
    threshold, window_count = CLIENT_THROTTLE_THRESHOLDS[client]
    assert threshold == pytest.approx(expected[0], abs=1e-9)
    assert window_count == expected[1]


def test_throttle_threshold_tuple_structure() -> None:
    """Each entry in CLIENT_THROTTLE_THRESHOLDS must be a (float, int) tuple."""
    for client, value in CLIENT_THROTTLE_THRESHOLDS.items():
        assert isinstance(value, tuple), f"{client!r}: expected tuple, got {type(value)}"
        assert len(value) == 2, f"{client!r}: expected 2-tuple"
        threshold, window_count = value
        assert isinstance(threshold, float), f"{client!r}: threshold must be float"
        assert isinstance(window_count, int), f"{client!r}: window_count must be int"
        assert 0.0 < threshold < 1.0, f"{client!r}: threshold must be in (0, 1)"
        assert window_count >= 1, f"{client!r}: window_count must be >= 1"


def test_copilot_min_n_constant() -> None:
    """COPILOT_THROTTLE_MIN_N must be 50 (larger sample required for copilot)."""
    assert COPILOT_THROTTLE_MIN_N == 50


def test_default_throttle_min_n_constant() -> None:
    """DEFAULT_THROTTLE_MIN_N must be 30 (statistical-significance floor)."""
    assert DEFAULT_THROTTLE_MIN_N == 30


def test_copilot_min_n_larger_than_default() -> None:
    """Copilot requires a larger minimum N than the default (low coverage justification)."""
    assert COPILOT_THROTTLE_MIN_N > DEFAULT_THROTTLE_MIN_N
