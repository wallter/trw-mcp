"""FR-11 — profile explanation contract test.

PRD-CORE-300 S11b deleted the profile-explain MCP tool; the service it
called (``profile.explain_surface``) is unchanged and is now reached through
``trw_status(detail="surface")`` and the ``trw-mcp profile explain`` CLI. This
file exercises ``explain_surface`` directly rather than through the deleted
tool wrapper. ``tests/test_status_surface_detail.py`` covers the
``trw_status(detail="surface")``/CLI plumbing and the reviewer-role bound; this
file keeps the per-field attribution coverage (org/domain layer provenance)
that file does not exercise.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.profile.explain import explain_surface


def _explain(tmp_path: Path, *, domain: str | None = None) -> dict[str, Any]:
    config = TRWConfig(trw_dir=str(tmp_path / ".trw"))
    return explain_surface(config, run_dir=None, trw_dir=tmp_path / ".trw", domain=domain)


def test_profile_explain_returns_payload(tmp_path: Path) -> None:
    """FR-11: explain_surface returns the structured explain payload."""
    payload = _explain(tmp_path)
    assert "fields" in payload
    assert "layers_applied" in payload
    assert "tool_surface" in payload
    # Each field record carries the attribution contract.
    for record in payload["fields"]:
        assert set(record) == {"field", "value", "origin_layer", "override_chain"}


@pytest.mark.parametrize("filename,domain,origin", [("org", None, "org"), ("domain-frontend", "frontend", "domain")])
def test_profile_explain_attributes_layer_values(
    tmp_path: Path, filename: str, domain: str | None, origin: str
) -> None:
    """FR-11: org and contextual domain values retain their attribution."""
    profiles = tmp_path / ".trw" / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    (profiles / f"{filename}.yaml").write_text("build_check_scope: targeted\n", encoding="utf-8")
    payload = _explain(tmp_path, domain=domain)
    assert origin in payload["layers_applied"]
    bcs = next(f for f in payload["fields"] if f["field"] == "build_check_scope")
    assert bcs["origin_layer"] == origin
    assert bcs["value"] == "targeted"
