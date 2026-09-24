"""Tests for cross-client meta-tune contract constants (PRD-DIST-2400 FR22-FR24).

Covers JOIN_KEY_FIELDS, DEFAULT_CORRELATION_WINDOW_SECONDS,
and CLIENT_CORRECTION_FACTORS.
"""

from __future__ import annotations

import pytest

from trw_mcp.channels._manifest_models import (
    CLIENT_CORRECTION_FACTORS,
    DEFAULT_CORRELATION_WINDOW_SECONDS,
    JOIN_KEY_FIELDS,
)
from trw_mcp.channels.meta_tune._correlator import adjusted_rate
from trw_mcp.models.config import builtin_client_ids

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
    "grok": 0.50,
}


def test_correction_factor_clients_present() -> None:
    """EVERY active client profile must have a correction factor defined.

    Derived from the profile registry, not from ``_EXPECTED_CORRECTION_FACTORS``.
    Iterating the expected dict made this test tautological for the one case it
    exists to catch: an eighth profile absent from BOTH dicts passed, and
    ``adjusted_rate`` then divided its observed rate by the 1.0 default while
    every other client was scaled — a silently incomparable number.
    """
    clients = builtin_client_ids()
    assert len(clients) >= len(_EXPECTED_CORRECTION_FACTORS), (
        "client registry derivation shrank below the known profile count; "
        "a truncated population makes this completeness check vacuous"
    )
    for client in clients:
        assert client in CLIENT_CORRECTION_FACTORS, (
            f"Missing correction factor for {client!r}. A client with no factor is scaled by 1.0 "
            "while every other client is corrected, so its reported rate is not comparable."
        )


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
