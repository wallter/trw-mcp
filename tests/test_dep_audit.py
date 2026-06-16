"""PRD-QUAL-110-FR03: no phantom dep_audit config flags.

``dep_audit_enabled`` (and its siblings) advertised a dependency-audit gate
that has no implementation anywhere in ``trw_mcp/src`` — the only references
were in dead, non-collecting test files. A config flag that advertises a
capability without an implementation is a truthfulness defect, so the fields
are removed. ``extra="ignore"`` means an old config that still sets the key
loads gracefully (RISK-003).
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.config import TRWConfig, _fields_build


def test_dep_audit_fields_absent_from_schema() -> None:
    cfg = TRWConfig()
    for field in (
        "dep_audit_enabled",
        "dep_audit_level",
        "dep_audit_timeout_secs",
        "dep_audit_block_on_patchable_only",
    ):
        assert not hasattr(cfg, field), f"phantom flag {field} still present"


def test_dep_audit_absent_from_fields_source() -> None:
    """Regression guard: the field declarations are gone from the source."""
    src = Path(_fields_build.__file__).read_text(encoding="utf-8")
    assert "dep_audit_enabled" not in src
    assert "dep_audit_level" not in src


def test_old_config_setting_dep_audit_loads_gracefully() -> None:
    """An old config that still sets dep_audit_enabled is ignored, not rejected."""
    cfg = TRWConfig(dep_audit_enabled=True)  # type: ignore[call-arg]
    assert not hasattr(cfg, "dep_audit_enabled")
