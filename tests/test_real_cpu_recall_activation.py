"""Opt-in actual CPU model/SQLite/registered recall activation, not efficacy eval."""

import os
from pathlib import Path

import pytest

_MODEL = os.environ.get("TRW_REAL_EMBEDDING_MODEL")
pytestmark = pytest.mark.skipif(not _MODEL, reason="explicit local CPU-model fixture not selected")


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
