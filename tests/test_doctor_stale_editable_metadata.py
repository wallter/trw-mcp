"""B71-111: doctor names an editable install whose dist-info no longer matches its source.

The runtime reads each package's version from its source now, so a stale record is no longer a fault
there, but pip and uv still report the old version; the version_status row says so and gives the fix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.server import _doctor_version_status as row_module


def test_a_stale_editable_dist_info_is_named_with_its_source_version(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.metadata

    import trw_memory

    import trw_mcp

    monkeypatch.setattr(trw_mcp, "__version__", "7.1.0")
    monkeypatch.setattr(trw_memory, "__version__", "4.1.0")
    monkeypatch.setattr(importlib.metadata, "version", {"trw-mcp": "6.0.0", "trw-memory": "4.1.0"}.__getitem__)

    assert row_module.stale_editable_metadata() == ["trw-mcp: metadata 6.0.0, source 7.1.0"]


def test_the_version_status_row_warns_on_stale_metadata_and_passes_when_it_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "trw_mcp.server._subcommands_release.collect_version_status",
        lambda _root: {"compatible": True, "mismatches": []},
    )
    monkeypatch.setattr(row_module, "stale_editable_metadata", lambda: ["trw-memory: metadata 3.0.0, source 4.0.1"])
    status, message = row_module.version_status_row(tmp_path)
    assert status == "WARN" and "trw-memory: metadata 3.0.0, source 4.0.1" in message and "uv pip install -e" in message

    monkeypatch.setattr(row_module, "stale_editable_metadata", list)
    assert row_module.version_status_row(tmp_path)[0] == "PASS"
