"""PRD-QUAL-110-FR04: disclosed + gated embeddings HF download.

With ``embeddings_enabled=True`` the first model load triggers a
huggingface.co download of all-MiniLM-L6-v2. The warmup path now:

  * honors an offline switch (``TRW_OFFLINE`` master switch and/or
    ``HF_HUB_OFFLINE``) that suppresses the background download, and
  * emits a first-run log line disclosing the huggingface.co egress.
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
    # Force the warmup guard to see embeddings as enabled and not yet checked.
    monkeypatch.setattr(
        "trw_mcp.models.config.get_config",
        lambda: SimpleNamespace(embeddings_enabled=True),
    )
    _memory_connection._embedder_checked = False
    yield
    _memory_connection.reset_embedder()


def test_offline_switch_suppresses_warmup(monkeypatch: pytest.MonkeyPatch) -> None:
    """TRW_OFFLINE=1 must short-circuit warmup — no download thread started."""
    monkeypatch.setenv("TRW_OFFLINE", "1")
    started: list[bool] = []
    monkeypatch.setattr(
        _memory_connection.threading,
        "Thread",
        lambda *a, **k: (started.append(True), SimpleNamespace(start=lambda: None, is_alive=lambda: False))[1],
    )
    result = _memory_connection._schedule_embedder_warmup()
    assert result is False
    assert started == []


def test_hf_hub_offline_suppresses_warmup(monkeypatch: pytest.MonkeyPatch) -> None:
    """HF_HUB_OFFLINE=1 also suppresses the warmup download."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    started: list[bool] = []
    monkeypatch.setattr(
        _memory_connection.threading,
        "Thread",
        lambda *a, **k: (started.append(True), SimpleNamespace(start=lambda: None, is_alive=lambda: False))[1],
    )
    result = _memory_connection._schedule_embedder_warmup()
    assert result is False
    assert started == []


def test_warmup_discloses_egress_when_online(monkeypatch: pytest.MonkeyPatch) -> None:
    """When NOT offline, scheduling warmup discloses the huggingface.co egress."""
    started: list[bool] = []

    class _FakeThread:
        def __init__(self, *a: object, **k: object) -> None:
            started.append(True)

        def start(self) -> None:
            pass

        def is_alive(self) -> bool:
            return False

    monkeypatch.setattr(_memory_connection.threading, "Thread", _FakeThread)
    with capture_logs() as logs:
        result = _memory_connection._schedule_embedder_warmup()
    assert result is True
    assert started == [True]
    events = {e.get("event") for e in logs}
    assert "embedder_download_disclosure" in events
    disclosure = next(e for e in logs if e.get("event") == "embedder_download_disclosure")
    assert "huggingface" in str(disclosure).lower()


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
