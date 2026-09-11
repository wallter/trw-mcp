"""PRD-SEC-014-FR05: both public READMEs state the real embedding-egress story.

Both network-behavior tables used to say the model downloads on the *first*
operation, which a warm-cache measurement falsified, and neither said that
embedding egress is not governed by the consent flags — the exact inference an
operator who has read the PRD-SEC-004 design would otherwise make.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._layout import PACKAGE_ROOT, requires_monorepo

_REPO_ROOT = Path(__file__).resolve().parents[2]
_READMES = {
    "trw-mcp": PACKAGE_ROOT / "README.md",
    "trw-memory": _REPO_ROOT / "trw-memory" / "README.md",
}


def _read(package: str) -> str:
    path = _READMES[package]
    assert path.is_file(), f"required package README missing: {path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("package", ["trw-mcp", pytest.param("trw-memory", marks=requires_monorepo)])
def test_readme_documents_warm_cache_and_consent_independence(package: str) -> None:
    """FR05: the warm-cache invariant and the consent-flag independence are stated."""
    text = _read(package)
    lowered = text.lower()

    # (a) a complete local cache produces no Hub request at all
    assert "local hugging face cache" in lowered
    assert "zero** huggingface.co request" in lowered

    # (b) a fetch happens only when the cache cannot answer and no switch is set
    assert "TRW_OFFLINE" in text
    assert "HF_HUB_OFFLINE" in text

    # (c) egress is NOT governed by the consent flags
    assert "learning_sharing_enabled" in text
    assert "platform_telemetry_enabled" in text
    assert "independent of the consent flags" in lowered


@pytest.mark.parametrize("package", ["trw-mcp", pytest.param("trw-memory", marks=requires_monorepo)])
def test_readme_drops_the_falsified_first_operation_claim(package: str) -> None:
    """The measurement that motivated this PRD contradicted these sentences."""
    text = _read(package)
    assert "First vector operation downloads" not in text
    assert "First embedding operation downloads" not in text


@requires_monorepo
def test_trw_memory_readme_documents_the_remote_code_field() -> None:
    """FR05: the new security default is in the security-defaults table."""
    text = _read("trw-memory")
    assert "embedding_trust_remote_code" in text
    assert "embedding_trust_remote_code=False" in text
    assert "RemoteCodeNotPermittedError" in text


def test_trw_mcp_readme_points_at_the_doctor_row() -> None:
    """FR04/FR05: the operator is told where to read the live posture."""
    text = _read("trw-mcp")
    assert "embedding_egress" in text
    assert "cache-first" in text
    assert "network-capable" in text


def test_missing_packaged_readme_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(_READMES, "trw-mcp", tmp_path / "missing.md")
    with pytest.raises(AssertionError, match="required package README missing"):
        _read("trw-mcp")
