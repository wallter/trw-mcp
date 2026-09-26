"""A real trw-memory daemon for the daemon-store tests (PRD-CORE-298 FR01).

The daemon runs in its own process under an isolated ``TRW_USER_DIR``; an
existing empty local model directory keeps it keyword-only and off the network.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from trw_memory.daemon import DaemonPaths
from trw_memory.daemon._discovery import DaemonInfo, read_discovery_result

_START_DEADLINE_SECONDS = 30.0

#: The daemon with a deterministic stand-in for the model: no torch, no weights, a
#: space of its own. A text containing "unencodable" fails as a blank one does.
_HASH_EMBEDDER_BOOT = """
import hashlib
import trw_memory.embeddings as embeddings
from trw_memory.embeddings.provenance import EmbeddingSpace

class HashEmbedder:
    def __init__(self, model_name, dim):
        self._dim = dim
        self._space = EmbeddingSpace("d" * 64, "test-hash-encoder-v1", dim, model_name)
    def available(self):
        return True
    def dim(self):
        return self._dim
    def embedding_space(self):
        return self._space
    def embed(self, text):
        if "unencodable" in text:
            return None
        digest = hashlib.sha256(text.encode()).digest()
        return [0.01 + digest[i % 32] / 255.0 for i in range(self._dim)]
    def embed_query(self, text):
        return self.embed(text)
    def embed_batch(self, texts):
        return [self.embed(text) for text in texts]

embeddings.LocalEmbeddingProvider = HashEmbedder
from trw_memory.server import main
main(["serve", "http"])
"""


@contextmanager
def running_daemon(user_dir: Path, *, keyword_only: bool = True, hash_embedder: bool = False) -> Iterator[DaemonPaths]:
    """Start a daemon whose files live under *user_dir* and yield its paths.

    *keyword_only* (the test default) points the daemon at an empty local model
    directory; a benchmark passes ``False`` so it embeds as a user's daemon does.
    *hash_embedder* gives it a deterministic encoder instead of a model.
    """
    keyword_only = keyword_only and not hash_embedder
    model_dir = user_dir / "unavailable-local-model"
    model_dir.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "TRW_USER_DIR": str(user_dir),
        **({"MEMORY_EMBEDDING_MODEL": str(model_dir)} if keyword_only else {}),
        # One daemon serves every test in a worker, so its per-session write limiter
        # would carry state across tests; in-process, each test had a fresh store.
        # trw-memory tests the limiter itself.
        "MEMORY_MAX_MEMORY_WRITES_PER_MINUTE": "1000000",
    }
    # A file, not a pipe: nothing reads the daemon's output while a test runs,
    # and request logging from a long test would fill a pipe and block the daemon.
    log_path = user_dir / "daemon.log"
    with log_path.open("wb") as log:
        proc = subprocess.Popen(
            [sys.executable, "-c", _HASH_EMBEDDER_BOOT]
            if hash_embedder
            else [sys.executable, "-m", "trw_memory.server", "serve", "http"],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("TRW_USER_DIR", str(user_dir))
            paths = DaemonPaths.resolve()
        deadline = time.monotonic() + _START_DEADLINE_SECONDS
        while True:
            if proc.poll() is not None:
                pytest.fail(f"daemon exited early: {log_path.read_text(encoding='utf-8', errors='replace')}")
            if isinstance(read_discovery_result(paths), DaemonInfo):
                break
            if time.monotonic() > deadline:
                pytest.fail("daemon never published a discovery file")
            time.sleep(0.05)
        yield paths
    finally:
        proc.kill()
        proc.wait(timeout=30)
