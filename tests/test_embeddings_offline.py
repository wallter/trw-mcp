"""PRD-QUAL-110-FR04: disclosed + gated embeddings HF download.

With ``embeddings_enabled=True`` the first model load triggers a
huggingface.co download of the configured retrieval model. That load:

  * discloses the huggingface.co egress in a log line before it starts, and
  * claims no egress when an offline switch (``TRW_OFFLINE`` and/or
    ``HF_HUB_OFFLINE``) makes trw-memory load from the local cache only.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from structlog.testing import capture_logs

from trw_mcp.state import _memory_connection


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("TRW_OFFLINE", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    _memory_connection.reset_embedder()
    # The embedder load sees embeddings enabled and not yet checked.
    monkeypatch.setattr(
        "trw_mcp.models.config.get_config",
        lambda: SimpleNamespace(
            embeddings_enabled=True, retrieval_embedding_model="configured/test-model", retrieval_embedding_dim=384
        ),
    )
    _memory_connection._embedder_checked = False
    yield
    _memory_connection.reset_embedder()


class _StubProvider:
    """Stands in for trw-memory's provider: records the load and what was logged before it."""

    built: list[str] = []
    logged_before_build: list[object] = []
    logs: list[dict[str, object]] = []

    def __init__(self, *, model_name: str, dim: int) -> None:
        _StubProvider.built.append(model_name)
        _StubProvider.logged_before_build = [entry.get("event") for entry in _StubProvider.logs]

    def available(self) -> bool:
        return False

    def unavailable_reason(self) -> str:
        return "stub"


def _load_with_stub(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    _StubProvider.built = []
    monkeypatch.setattr("trw_memory.embeddings.local.LocalEmbeddingProvider", _StubProvider)
    with capture_logs() as logs:
        _StubProvider.logs = logs
        _memory_connection.get_embedder()
    assert _StubProvider.built == ["configured/test-model"]
    return logs


@pytest.mark.parametrize("switch", ["TRW_OFFLINE", "HF_HUB_OFFLINE"])
def test_an_offline_switch_leaves_no_egress_to_disclose(monkeypatch: pytest.MonkeyPatch, switch: str) -> None:
    """Offline, trw-memory loads from the cache only, so the load claims no download."""
    monkeypatch.setenv(switch, "1")

    logs = _load_with_stub(monkeypatch)

    assert "embedder_download_disclosure" not in {e.get("event") for e in logs}


def test_the_first_load_discloses_its_egress_before_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    logs = _load_with_stub(monkeypatch)

    assert "embedder_download_disclosure" in _StubProvider.logged_before_build, "disclosed after the load began"
    disclosure = next(e for e in logs if e.get("event") == "embedder_download_disclosure")
    assert "huggingface" in str(disclosure).lower()
    # The disclosure names the model the load will actually fetch, not a constant.
    assert disclosure["model"] == "configured/test-model"


def test_offline_env_helper() -> None:
    """The offline detector recognizes both switches and truthy values."""
    import os

    assert _memory_connection._embeddings_offline({"TRW_OFFLINE": "1"}) is True
    assert _memory_connection._embeddings_offline({"HF_HUB_OFFLINE": "true"}) is True
    assert _memory_connection._embeddings_offline({"TRW_OFFLINE": "0"}) is False
    assert _memory_connection._embeddings_offline({}) is False
    assert isinstance(_memory_connection._embeddings_offline(dict(os.environ)), bool)


def test_ceremony_behavior_server_defaults_embedding_downloads_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generic ceremony tests must not launch an unowned Hugging Face client."""
    import os

    from tests._ceremony_helpers import make_ceremony_server

    monkeypatch.delenv("TRW_OFFLINE", raising=False)
    make_ceremony_server(monkeypatch, tmp_path)

    assert os.environ["TRW_OFFLINE"] == "1"
