"""Opt-in actual CPU model/SQLite/registered recall activation, not efficacy eval."""

import asyncio
import os
from pathlib import Path

import pytest

_MODEL = os.environ.get("TRW_REAL_EMBEDDING_MODEL")
pytestmark = pytest.mark.skipif(not _MODEL, reason="explicit local CPU-model fixture not selected")


@pytest.mark.parametrize("compact", [False, True])
def test_real_cpu_model_repair_to_registered_recall(tmp_project, monkeypatch, compact):
    import torch
    from fastmcp import Client
    from trw_memory.embeddings.local import LocalEmbeddingProvider
    from trw_memory.models.memory import MemoryEntry

    from tests.conftest import make_test_server
    from trw_mcp.models.config import get_config
    from trw_mcp.state._embedding_repair import repair_page
    from trw_mcp.state.memory_adapter import get_backend

    assert torch.version.cuda is None, "activation fixture requires a CPU-only Torch build"
    assert _MODEL and Path(_MODEL).is_dir()
    torch.set_num_threads(2)
    provider = LocalEmbeddingProvider(_MODEL, dim=384)
    assert provider.available()
    space = provider.embedding_space()
    assert space is not None
    monkeypatch.setattr(get_config(), "embeddings_enabled", True)
    monkeypatch.setattr("trw_mcp.state._memory_connection.get_initialized_embedder", lambda: provider)
    backend = get_backend(tmp_project / ".trw")
    assert backend.vec_available
    for entry_id, content in [
        ("target", "Restore connectivity by restarting the router."),
        ("pie", "Bake an apple pie with cinnamon."),
        ("sql", "Use database transaction savepoints for atomic writes."),
    ]:
        backend.store(MemoryEntry(id=entry_id, content=content))
    assert repair_page(backend, provider, max_entries=3)["repaired"] == 3
    assert provider.embedding_space() == space  # encode's transient tokenizer state is not identity

    async def recall():
        async with Client(make_test_server("learning")) as client:
            result = await client.call_tool(
                "trw_recall",
                {"query": "network repair", "max_results": 1, "compact": compact, "include_tiers": ["project"]},
            )
            assert not result.is_error
            return result.structured_content

    response = asyncio.run(recall())
    assert [row["id"] for row in response["learnings"]] == ["target"]
    assert provider.embedding_space() == space


def test_identical_model_copy_and_different_batch_have_same_identity(tmp_path):
    import shutil

    import torch
    from trw_memory.embeddings.local import LocalEmbeddingProvider

    assert torch.version.cuda is None
    assert _MODEL and Path(_MODEL).is_dir()
    torch.set_num_threads(2)
    copied = tmp_path / "copied-model"
    shutil.copytree(_MODEL, copied)
    first = LocalEmbeddingProvider(_MODEL, dim=384)
    second = LocalEmbeddingProvider(str(copied), dim=384)
    assert first.available() and second.available()
    identity = first.embedding_space()
    assert identity is not None and second.embedding_space() == identity
    assert first.embed("network repair") is not None
    assert second.embed_batch(["A considerably longer sentence about restoring connectivity.", "short"])
    assert first.embedding_space() == second.embedding_space() == identity
