"""A real trw-memory daemon for the daemon-store tests (PRD-CORE-298 FR01).

No pytest import: ``benchmarks/engmem_mcp.py`` (the canary memory-soak) imports this (running_daemon, MemoryDaemon, attach_checkout) under a venv that has none.
The daemon runs in its own process under an isolated ``TRW_USER_DIR``; an
existing empty local model directory keeps it keyword-only and off the network.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from trw_memory.daemon import DaemonPaths, mint_grant, write_checkout_grant
from trw_memory.daemon._discovery import DaemonInfo, read_discovery_result
from trw_memory.daemon.client import DaemonClient

from trw_mcp.state._tier_routing import USER_NAMESPACE

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


#: Cache locations that would override the keyword-only daemon's own empty hub cache.
_OTHER_MODEL_CACHES = frozenset({"HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "SENTENCE_TRANSFORMERS_HOME"})


@contextmanager
def running_daemon(user_dir: Path, *, keyword_only: bool = True, hash_embedder: bool = False) -> Iterator[DaemonPaths]:
    """Start a daemon whose files live under *user_dir* and yield its paths.

    *keyword_only* (the test default) points the daemon at a model id its own, empty
    hub cache does not hold, so the cache probe refuses it before any model runtime is
    imported (EMBED-PROBE-FAST-PATH; an empty model directory cost each session daemon a
    ~4 s torch import on its first embed); a benchmark passes ``False`` so it embeds as a
    user's daemon does. *hash_embedder* gives it a deterministic encoder instead of a model.
    """
    keyword_only = keyword_only and not hash_embedder
    hub_cache = user_dir / "empty-hub-cache"
    hub_cache.mkdir(parents=True, exist_ok=True)
    inherited = {k: v for k, v in os.environ.items() if not keyword_only or k not in _OTHER_MODEL_CACHES}
    env = {
        **inherited,
        "TRW_USER_DIR": str(user_dir),
        **(
            {"MEMORY_EMBEDDING_MODEL": "trw-test/keyword-only-no-model", "HF_HOME": str(hub_cache)}
            if keyword_only
            else {}
        ),
        # One daemon serves every test in a worker, so its per-session write limiter
        # would carry state across tests; in-process, each test had a fresh store.
        # trw-memory tests the limiter itself.
        "MEMORY_MAX_MEMORY_WRITES_PER_MINUTE": "1000000",
        # The session-wide 60 s idle cap (conftest, DAEMON-ORPHAN-SPAWN) is for daemons a test
        # auto-starts. This one must outlive a worker's longest stretch without memory calls:
        # on a loaded 4-core runner that exceeded 60 s, the daemon exited mid-session and every
        # later daemon_checkout test errored. The session-end owner reap still stops it.
        "MEMORY_DAEMON_IDLE_SHUTDOWN_SECONDS": "1800",
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
        with mock.patch.dict(os.environ, {"TRW_USER_DIR": str(user_dir)}):
            paths = DaemonPaths.resolve()
        deadline = time.monotonic() + _START_DEADLINE_SECONDS
        while True:
            if proc.poll() is not None:
                raise RuntimeError(f"daemon exited early: {log_path.read_text(encoding='utf-8', errors='replace')}")
            if isinstance(read_discovery_result(paths), DaemonInfo):
                break
            if time.monotonic() > deadline:
                raise RuntimeError("daemon never published a discovery file")
            time.sleep(0.05)
        yield paths
    finally:
        proc.kill()
        proc.wait(timeout=30)


@dataclass(frozen=True)
class MemoryDaemon:
    """The session daemon: its paths and the ``TRW_USER_DIR`` it runs under."""

    paths: DaemonPaths
    user_dir: Path


def attach_checkout(trw_dir: Path, daemon: MemoryDaemon) -> tuple[str, DaemonClient]:
    """Pin *trw_dir* to a fresh namespace and grant it plus ``user:local``; returns (namespace, client)."""
    namespace = f"project:t{uuid.uuid4().hex[:12]}"
    trw_dir.mkdir(parents=True, exist_ok=True)
    config = trw_dir / "config.yaml"
    existing = config.read_text(encoding="utf-8") if config.exists() else ""
    config.write_text(f"{existing}project_namespace: {namespace}\n", encoding="utf-8")
    token = mint_grant(daemon.paths, [namespace, USER_NAMESPACE], root=trw_dir.parent)
    write_checkout_grant(trw_dir, token)
    return namespace, DaemonClient(token, paths=daemon.paths)
