"""PRD-QUAL-110-FR05/FR06: README disclosure + defaults match code.

FR05 (regression guard): the trw-mcp README config block must keep the REAL
defaults (``embeddings_enabled: true``, ``learning_max_entries: 500``) so the
already-corrected docs do not drift back.

FR06: the README must carry the four disclosure surfaces — a "Telemetry &
network behavior" section, an env-var inventory, a security-defaults table, and
an enterprise hardening recipe.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig

_README = Path(__file__).resolve().parents[1] / "README.md"


@pytest.fixture(scope="module")
def readme_text() -> str:
    return _README.read_text(encoding="utf-8")


def test_readme_embeddings_default_matches_code(readme_text: str) -> None:
    """FR05: README config block shows embeddings_enabled: true (real default)."""
    assert "embeddings_enabled: true" in readme_text
    assert TRWConfig().embeddings_enabled is True


def test_readme_learning_max_entries_matches_code(readme_text: str) -> None:
    """FR05: README shows learning_max_entries: 500 (real default)."""
    assert "learning_max_entries: 500" in readme_text
    assert TRWConfig().learning_max_entries == 500


def test_readme_has_telemetry_network_section(readme_text: str) -> None:
    """FR06(a): the Telemetry & network behavior heading is present."""
    assert "## Telemetry & network behavior" in readme_text


def test_readme_has_env_var_inventory(readme_text: str) -> None:
    """FR06(b): env-var inventory covers the required variables."""
    for var in (
        "TRW_OFFLINE",
        "TRW_PROBE_ENABLED",
        "TRW_EMBEDDINGS_AVAILABLE",
        "ENABLE_TOOL_SEARCH",
        "TRW_PLATFORM_API_KEY",
        "MEMORY_*",
    ):
        assert var in readme_text, f"env var {var} missing from README inventory"


def test_readme_has_security_defaults_table(readme_text: str) -> None:
    """FR06(c): a security-defaults table with the documented postures."""
    lowered = readme_text.lower()
    assert "security defaults" in lowered
    assert "pii redaction" in lowered
    assert "observe" in lowered  # poisoning observe-mode
    assert "0700" in readme_text and "0600" in readme_text


def test_readme_has_hardening_recipe(readme_text: str) -> None:
    """FR06(d): an enterprise hardening recipe referencing the offline switch."""
    assert "hardening recipe" in readme_text.lower()
    assert "TRW_OFFLINE=1" in readme_text
