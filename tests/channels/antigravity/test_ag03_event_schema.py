"""RC-013: the installed AG-03 hook writes a valid channel-event/v1 record.

The hook script builds its own event dict (it runs inside Antigravity, not inside
trw-mcp), so nothing structural keeps it in step with ``_telemetry``. It used to write
``"schema"`` instead of ``"schema_version"`` and no ``event_type``, so the meta-tune
correlator dropped every AG-03 event. This runs the INSTALLED script as a real process.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from trw_mcp.channels._telemetry import CHANNEL_EVENT_SCHEMA_VERSION, CHANNEL_EVENT_V1_REQUIRED, VALID_EVENT_TYPES
from trw_mcp.channels.antigravity._before_edit_hook import install_before_edit_hook
from trw_mcp.channels.meta_tune._correlator import PUSH_EVENT_TYPES


@pytest.mark.unit
def test_installed_hook_writes_a_valid_channel_event(tmp_path: Path) -> None:
    result = install_before_edit_hook(tmp_path)
    assert result["installed"] is True, result
    script = Path(str(result["hook_script_path"]))
    out = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps({"tool_name": "write_file", "file_path": "src/a.py"}),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert json.loads(out.stdout) == {"continue": True}, "the hook stays fail-open"
    lines = (tmp_path / ".trw" / "telemetry" / "channel-events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    missing = [key for key in CHANNEL_EVENT_V1_REQUIRED if not event.get(key)]
    assert missing == [], f"missing required fields: {missing}"
    assert "schema" not in event
    assert event["schema_version"] == CHANNEL_EVENT_SCHEMA_VERSION
    assert event["event_type"] in VALID_EVENT_TYPES
    assert event["event_type"] in PUSH_EVENT_TYPES, "the correlator must classify it, not drop it"
    assert (event["tool_name"], event["file_path"]) == ("write_file", "src/a.py")
