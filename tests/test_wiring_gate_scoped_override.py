"""PRD-CORE-231-FR04: category-scoped wiring-gate mode.

``wiring_gate_mode`` was a single repo-wide field, so piloting ``block`` meant
flipping it for every PRD in the catalogue at once with no scoped rollback.
These tests pin the resolver that now runs before the global default.
"""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.validation._prd_quality_refresh import _resolve_wiring_mode


def _config(**overrides: object) -> TRWConfig:
    return TRWConfig(**overrides)  # type: ignore[arg-type]


def test_category_override_blocks() -> None:
    """A CORE PRD picks up block mode while the global default stays warn."""
    config = _config(wiring_gate_mode="warn", wiring_gate_mode_overrides={"CORE": "block"})

    assert _resolve_wiring_mode(config, {"category": "CORE"}) == "block"


def test_unlisted_category_falls_back_to_global() -> None:
    """A QUAL PRD under the same config keeps the advisory global mode."""
    config = _config(wiring_gate_mode="warn", wiring_gate_mode_overrides={"CORE": "block"})

    assert _resolve_wiring_mode(config, {"category": "QUAL"}) == "warn"
    assert _resolve_wiring_mode(config, {"category": "INFRA"}) == "warn"
    assert _resolve_wiring_mode(config, {"category": "DIST"}) == "warn"


@pytest.mark.parametrize("frontmatter_category", ["core", "CORE", "Core", "  core  "])
def test_lookup_is_case_and_whitespace_insensitive(frontmatter_category: str) -> None:
    """`core` and `CORE` must not register as distinct entries."""
    config = _config(wiring_gate_mode="warn", wiring_gate_mode_overrides={"CORE": "block"})

    assert _resolve_wiring_mode(config, {"category": frontmatter_category}) == "block"


@pytest.mark.parametrize("override_key", ["core", "CORE", "Core"])
def test_override_key_case_is_normalized(override_key: str) -> None:
    """The override side of the comparison is normalized too, not just frontmatter."""
    config = _config(wiring_gate_mode="warn", wiring_gate_mode_overrides={override_key: "block"})

    assert _resolve_wiring_mode(config, {"category": "CORE"}) == "block"


def test_empty_overrides_reproduce_pre_fr04_behavior() -> None:
    """Regression guard: the default config must behave exactly as before."""
    config = _config(wiring_gate_mode="warn")

    assert config.wiring_gate_mode_overrides == {}
    for category in ("CORE", "QUAL", "INFRA", "FIX", "DIST", ""):
        assert _resolve_wiring_mode(config, {"category": category}) == "warn"


def test_override_can_relax_a_blocking_global() -> None:
    """Scoping works in both directions — an override may also downgrade to warn."""
    config = _config(wiring_gate_mode="block", wiring_gate_mode_overrides={"DIST": "warn"})

    assert _resolve_wiring_mode(config, {"category": "DIST"}) == "warn"
    assert _resolve_wiring_mode(config, {"category": "CORE"}) == "block"


def test_missing_category_falls_back_to_global() -> None:
    """A PRD with no category in frontmatter is never silently blocked."""
    config = _config(wiring_gate_mode="warn", wiring_gate_mode_overrides={"CORE": "block"})

    assert _resolve_wiring_mode(config, {}) == "warn"
    assert _resolve_wiring_mode(config, {"category": ""}) == "warn"
    assert _resolve_wiring_mode(config, {"category": None}) == "warn"


def test_resolver_is_wired_into_the_validation_path() -> None:
    """Delivered != wired: the refresh module must actually call the resolver."""
    import inspect

    from trw_mcp.state.validation import _prd_quality_refresh as refresh

    source = inspect.getsource(refresh.refresh_dynamic_prd_validation)
    assert "_resolve_wiring_mode(" in source
    assert 'getattr(scaled_config, "wiring_gate_mode", "warn")' not in source
