"""Consent-stamp behavior lock for the legacy TelemetryClient flush path.

PRD-SEC-004 (sweep-4 finding 2): ``TelemetryClient.flush`` stamps every
flushed record with ``stamp_consent(record, consented=platform_telemetry_enabled)``
so the sender uploads only consented rows and never the pre-consent backlog
after a later opt-in. That wiring was previously only line-covered — a
regression hardcoding ``consented=True`` (which would upload the pre-consent
backlog) would have passed the existing tests.

These tests read the actual JSONL and assert the persisted ``_trw_consent``
marker tracks the platform consent flag in BOTH directions. They do not mock
``stamp_consent`` — they assert the real persisted value.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests._telemetry_client_support import _base_event
from trw_mcp.telemetry.client import TelemetryClient
from trw_mcp.telemetry.sender import _CONSENT_FIELD


def _flush_and_read(output: Path) -> list[dict[str, object]]:
    lines = [line for line in output.read_text().splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


class TestTelemetryClientConsentStamp:
    def test_flush_stamps_consent_false_when_platform_disabled(self, tmp_path: Path) -> None:
        """platform_telemetry_enabled=False -> every flushed row is _trw_consent False."""
        output = tmp_path / "logs" / "telemetry.jsonl"
        output.parent.mkdir(parents=True, exist_ok=True)
        client = TelemetryClient(
            enabled=True,
            output_path=output,
            platform_telemetry_enabled=False,
        )
        client.record_event(_base_event())
        client.record_event(_base_event())
        written = client.flush()

        assert written == 2
        records = _flush_and_read(output)
        assert len(records) == 2
        # The compliance invariant: a pre-consent row is marked NOT uploadable.
        for record in records:
            assert record[_CONSENT_FIELD] is False

    def test_flush_stamps_consent_true_when_platform_enabled(self, tmp_path: Path) -> None:
        """platform_telemetry_enabled=True -> every flushed row is _trw_consent True."""
        output = tmp_path / "logs" / "telemetry.jsonl"
        output.parent.mkdir(parents=True, exist_ok=True)
        client = TelemetryClient(
            enabled=True,
            output_path=output,
            platform_telemetry_enabled=True,
        )
        client.record_event(_base_event())
        client.flush()

        records = _flush_and_read(output)
        assert len(records) == 1
        assert records[0][_CONSENT_FIELD] is True

    def test_default_consent_is_false_when_flag_omitted(self, tmp_path: Path) -> None:
        """Omitting the flag fails closed: rows are stamped not-uploadable."""
        output = tmp_path / "logs" / "telemetry.jsonl"
        output.parent.mkdir(parents=True, exist_ok=True)
        # Construct WITHOUT platform_telemetry_enabled — the constructor default
        # must be the fail-closed (False) consent value.
        client = TelemetryClient(enabled=True, output_path=output)
        client.record_event(_base_event())
        client.flush()

        records = _flush_and_read(output)
        assert len(records) == 1
        assert records[0][_CONSENT_FIELD] is False
