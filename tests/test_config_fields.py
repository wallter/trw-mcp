"""Architecture guard tests for PRD-CORE-089: config field split.

Ensures the config decomposition maintains structural contracts:
- _main_fields.py stays thin (< 200 lines)
- All domain mixin files exist
- Flat field access works through inheritance
- Environment variable overrides resolve through mixins
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


# -- T01: File size gate --


def test_main_fields_under_200_lines() -> None:
    """_main_fields.py must stay under 200 lines (assembly shell only)."""
    config_dir = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "models" / "config"
    main_fields = config_dir / "_main_fields.py"
    assert main_fields.exists(), f"Expected {main_fields} to exist"
    line_count = len(main_fields.read_text(encoding="utf-8").splitlines())
    assert line_count < 200, f"_main_fields.py has {line_count} lines, expected < 200"


def test_domain_mixin_files_exist() -> None:
    """All 8 domain mixin files must exist."""
    config_dir = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "models" / "config"
    expected = [
        "_fields_scoring.py",
        "_fields_memory.py",
        "_fields_orchestration.py",
        "_fields_telemetry.py",
        "_fields_ceremony.py",
        "_fields_build.py",
        "_fields_trust.py",
        "_fields_paths.py",
    ]
    for filename in expected:
        assert (config_dir / filename).exists(), f"Missing domain mixin: {filename}"


def test_domain_mixin_files_under_200_lines() -> None:
    """Each domain mixin file must be under 200 lines."""
    config_dir = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "models" / "config"
    for path in sorted(config_dir.glob("_fields_*.py")):
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        assert line_count < 200, f"{path.name} has {line_count} lines, expected < 200"


# -- T02: Backward-compatible flat field access --


def test_all_flat_field_access_works() -> None:
    """Representative flat field access through TRWConfig must work."""
    from trw_mcp.models.config import get_config

    config = get_config()

    # One field from each domain mixin
    assert isinstance(config.scoring_default_days_unused, int)  # scoring
    assert isinstance(config.learning_max_entries, int)  # memory
    assert isinstance(config.parallelism_max, int)  # orchestration
    assert isinstance(config.debug, bool)  # telemetry
    assert isinstance(config.ceremony_mode, str)  # ceremony
    assert isinstance(config.build_check_enabled, bool)  # build
    assert isinstance(config.trust_crawl_boundary, int)  # trust
    assert isinstance(config.trw_dir, str)  # paths

    # Verify sub-config access still works
    assert isinstance(config.build.build_check_enabled, bool)
    assert isinstance(config.memory.learning_max_entries, int)


# -- T03: Environment variable override --


def test_env_var_override_via_domain_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """TRW_ env var overrides must resolve through domain mixin fields."""
    from trw_mcp.models.config._loader import _reset_config

    monkeypatch.setenv("TRW_SCORING_DEFAULT_DAYS_UNUSED", "777")
    _reset_config()

    from trw_mcp.models.config import get_config

    config = get_config()
    assert config.scoring_default_days_unused == 777

    # Clean up
    _reset_config()
