from __future__ import annotations

from datetime import datetime, timezone

from trw_mcp.telemetry.models import TelemetryEvent

_INSTALL_ID = "abcd1234abcd1234"
_FW_VERSION = "v21.0_TRW"


def _base_event(**kwargs: object) -> TelemetryEvent:
    return TelemetryEvent(
        installation_id=_INSTALL_ID,
        framework_version=_FW_VERSION,
        event_type="test_event",
        **kwargs,  # type: ignore[arg-type]
    )
