"""F-01 — loader contract: legacy shim, deprecation log, fail-closed (PRD-HPO-PROF-001 FR-5/10/12).

Companion to ``tests/integration/profile/test_loader.py``. This file pins the
audit-named loader contract:

  * the legacy ``cursor`` -> ``cursor-cli`` shim roundtrips to a client layer,
  * remapping emits a deprecation log (asserted via ``capture_logs``),
  * malformed YAML raises ``LayerLoadError`` CARRYING the offending path,
  * a schema-invalid layer fails closed (no silent fallback to defaults).

The malformed/schema cases write a layer file, so this file is integration-tier
(uses ``tmp_path``) even though it lives under ``unit/profile/``.

(Named ``test_loader_contract.py`` rather than ``test_loader.py`` to avoid a
pytest ``prepend``-import-mode basename collision with the existing
``tests/integration/profile/test_loader.py``.)
"""

from __future__ import annotations

from pathlib import Path

import pytest
import structlog.testing

from trw_mcp.profile import (
    LayerLoadError,
    load_layer,
    translate_legacy_client_profile,
)


def test_legacy_cursor_shim_roundtrips_to_client_layer() -> None:
    """FR-10: the bare 'cursor' id remaps to a 'cursor-cli' client layer."""
    layer = translate_legacy_client_profile("cursor")
    assert layer.name == "client"
    assert "cursor-cli" in (layer.source_path or "")


def test_legacy_remap_emits_deprecation_log() -> None:
    """FR-10: remapping a legacy key emits a single deprecation warning."""
    with structlog.testing.capture_logs() as logs:
        translate_legacy_client_profile("cursor")
    remap_events = [e for e in logs if e["event"] == "profile_legacy_client_remap"]
    assert len(remap_events) == 1
    assert remap_events[0]["legacy_client_id"] == "cursor"
    assert remap_events[0]["resolved_client_id"] == "cursor-cli"


def test_current_client_id_does_not_emit_remap_log() -> None:
    """FR-10: a current (non-legacy) client id does not trigger the warning."""
    with structlog.testing.capture_logs() as logs:
        translate_legacy_client_profile("claude-code")
    assert not any(e["event"] == "profile_legacy_client_remap" for e in logs)


def test_malformed_yaml_raises_layer_load_error_with_path(tmp_path: Path) -> None:
    """FR-12: malformed YAML raises LayerLoadError carrying the offending path."""
    bad = tmp_path / "org.yaml"
    bad.write_text("review_threshold: [unclosed\n", encoding="utf-8")
    with pytest.raises(LayerLoadError) as exc:
        load_layer("org", bad)
    assert str(bad) in str(exc.value)
    assert exc.value.path == str(bad)


def test_schema_invalid_layer_fails_closed_no_silent_fallback(tmp_path: Path) -> None:
    """FR-12: an unknown key fails closed (LayerLoadError), never to defaults."""
    bad = tmp_path / "org.yaml"
    bad.write_text("totally_unknown_key: 1\n", encoding="utf-8")
    with pytest.raises(LayerLoadError) as exc:
        load_layer("org", bad)
    assert exc.value.path == str(bad)
    assert "schema validation failed" in exc.value.reason
