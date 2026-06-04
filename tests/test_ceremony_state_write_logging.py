"""write_ceremony_state surfaces a silent persistence failure (audit A-P1-06).

The OSError swallow stays fail-open (a write failure must not crash a tool) but
now emits a ``ceremony_state_write_failed`` warning — the delivery gate reads
this state back (build_check_result / session_started), so a silently-swallowed
write failure would mis-fire the gate (e.g. block a passing build).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import structlog


def test_write_ceremony_state_logs_warning_on_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.state import _ceremony_progress_state as mod

    def _boom(_src: object, _dst: object) -> None:
        raise OSError("disk full")

    # Make the atomic os.replace fail; the function must stay fail-open.
    monkeypatch.setattr(mod.os, "replace", _boom)

    with structlog.testing.capture_logs() as logs:
        # Must NOT raise — a persistence failure cannot crash a tool.
        mod.write_ceremony_state(tmp_path, mod.CeremonyState())

    failures = [e for e in logs if e.get("event") == "ceremony_state_write_failed"]
    assert failures, f"expected ceremony_state_write_failed warning; got {logs}"
    assert failures[0]["log_level"] == "warning"
    assert "state_path" in failures[0]
