"""PRD-CORE-302 FR07 / PLAN W06: migrated 5.x rows regain dense recall with the REAL model.

``test_store_migration.py`` proves the re-embed contract with a hash encoder. This
proves the claim that matters with ``BAAI/bge-small-en-v1.5`` from the local cache.
It is not collected by the suite (the file name does not match ``test_*.py``):

    PYTHONPATH=<tree>/trw-mcp/src:<tree>/trw-memory/src \\
      .venv/bin/python -m pytest -q -s -p no:cacheprovider tests/bench_w06_real_reembed.py

The fixture is a project store as a 5.x in-process embedder left it: every vector in
an old space, the proofs computed over the stripped summary (so rows whose content
has trailing whitespace or a newline carry a false claim), and one row written
before provenance existed. After ``memory migrate --apply`` and the re-embed:

- the count of vectors outside the active space goes from all of them to zero,
  as the daemon's status and doctor's note both report;
- every stored vector is the model's own encoding of the row's text (cosine ~1);
- dense ranking alone puts each target first for a paraphrase that shares no word
  with it, so no lexical lane can be what finds it;
- daemon recall of that paraphrase returns the target, which it did not before.

The receipt is ``docs/sprint-mcp7/receipts/w06-real-reembed.md``.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import pwd
from pathlib import Path

import pytest
from trw_memory.models.memory import MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend

_MODEL = "BAAI/bge-small-en-v1.5"


#: id -> (content, a paraphrase that shares no word with it after stemming).
_TARGETS = {
    "L-clean": ("Pin the sqlite driver version before running schema migrations", "database upgrade ordering"),
    "L-trailing": ("Rotate the signing keys every ninety days ", "credential renewal schedule"),
    "L-newline": ("Cache invalidation happens\nwhen the upstream etag changes", "stale data refresh trigger"),
    "L-legacy": ("The payment webhook retries with exponential backoff", "billing callback resend delays"),
}
_DISTRACTORS = [
    "Use structlog and never pass event= as a keyword",
    "Pydantic models need populate_by_name when fields have aliases",
    "The frontend build uses Next.js with Tailwind",
    "Run only the changed test file during implementation",
    "Docker images must pin the base digest",
    "Release trw-memory before trw-mcp",
    "YAML reads use the safe loader",
    "The installer writes a managed-artifacts manifest",
    "Sub-agents return findings as text",
    "Coverage threshold is 85 percent",
    "Git commits in a shared tree are path scoped",
    "The recall response is compact by default",
]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def _five_x_checkout(root: Path) -> dict[str, str]:
    """Rows, old-space vectors, proofs over stripped text, and one row with no proof."""
    from trw_memory.embeddings.provenance import EmbeddingSpace, VectorProvenance

    (root / ".trw" / "memory").mkdir(parents=True)
    (root / ".trw" / "config.yaml").write_text("task_root: docs\n", encoding="utf-8")
    rows = {entry_id: content for entry_id, (content, _) in _TARGETS.items()}
    rows.update({f"L-d{index:02d}": text for index, text in enumerate(_DISTRACTORS)})
    store = SQLiteBackend(root / ".trw" / "memory" / "memory.db")
    try:
        old = EmbeddingSpace("a" * 64, "trw-declared-encoder-v1:all-MiniLM-L6-v2", store._dim)
        for index, (entry_id, content) in enumerate(rows.items()):
            store.store(MemoryEntry(id=entry_id, content=content, namespace="default", sync_seq=index))
            vector = [0.0] * store._dim
            vector[index] = 1.0
            proof = None if entry_id == "L-legacy" else VectorProvenance.for_vector(old, content.strip(), vector)
            store.upsert_vector(entry_id, vector, namespace="default", provenance=proof)
    finally:
        store.close()
    return rows


def _recalled_ids(result: dict[str, object]) -> list[str]:
    assert "dense" not in result, f"the daemon refused dense recall: {result['dense']}"
    rows = result["memories"]
    assert isinstance(rows, list)
    return [str(row["id"]) for row in rows]


def test_migrated_rows_regain_dense_recall_with_the_real_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_memory.embeddings.local import LocalEmbeddingProvider

    from tests._memory_daemon import running_daemon
    from tests.test_store_migration import _client, _namespace
    from trw_mcp.models.config import reload_config
    from trw_mcp.state import _store_migration
    from trw_mcp.state._retrieval_capability import outside_active_space_note

    # The suite points HOME and HF_HOME at a per-test home, so ``~`` would name that
    # home; this measures the real cache.
    real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    monkeypatch.setenv("HF_HOME", os.environ.get("W06_HF_HOME", str(real_home / ".cache" / "huggingface")))
    monkeypatch.setenv("MEMORY_EMBEDDING_MODEL", _MODEL)
    user_dir = tmp_path / "user"
    monkeypatch.setenv("TRW_USER_DIR", str(user_dir))

    def _no_autostart(_paths: object) -> None:
        raise AssertionError("the bench started a second daemon")

    monkeypatch.setattr("trw_memory.daemon.client.start_daemon_detached", _no_autostart)
    reload_config()
    root = tmp_path / "repo"
    rows = _five_x_checkout(root)
    out: dict[str, object] = {"rows": len(rows), "model": _MODEL}

    with running_daemon(user_dir, keyword_only=False):
        _store_migration.apply_migration(root / ".trw")
        namespace = _namespace(root)
        client = _client(root)

        async def recall_all() -> dict[str, list[str]]:
            return {
                entry_id: _recalled_ids(await client.recall(query, namespace))
                for entry_id, (_, query) in _TARGETS.items()
            }

        # The daemon loads its model lazily (OD10), so the count is unmeasured until
        # the first dense request; recall first, then read it.
        out["doctor_unloaded"] = outside_active_space_note(root / ".trw")
        before = asyncio.run(recall_all())
        out["outside_before"] = asyncio.run(client.status(namespace))["coverage"]["outside_active_space"]
        out["doctor_before"] = outside_active_space_note(root / ".trw")

        result = _store_migration.reembed_checkout(root / ".trw")
        out["reembed"] = {k: result[k] for k in ("status", "reembedded", "already_current", "outside_active_space")}
        out["outside_after"] = asyncio.run(client.status(namespace))["coverage"]["outside_active_space"]
        out["doctor_after"] = outside_active_space_note(root / ".trw")
        after = asyncio.run(recall_all())
        stored = asyncio.run(client.vectors(namespace, list(rows)))["vectors"]

    model = LocalEmbeddingProvider(_MODEL)
    fidelity = {entry_id: _cosine(stored[entry_id], model.embed(f"{text} ") or []) for entry_id, text in rows.items()}
    dense_rank = {}
    for entry_id, (_, query) in _TARGETS.items():
        q = model.embed_query(query) or []
        ranked = sorted(stored, key=lambda other: _cosine(q, stored[other]), reverse=True)
        dense_rank[entry_id] = ranked.index(entry_id) + 1
    out["min_vector_fidelity"] = round(min(fidelity.values()), 6)
    out["dense_rank_of_target"] = dense_rank
    out["recall_before_hit"] = {k: k in v for k, v in before.items()}
    out["recall_after_hit"] = {k: k in v for k, v in after.items()}
    out["recall_after_rank"] = {k: (v.index(k) + 1 if k in v else None) for k, v in after.items()}
    print("W06 " + json.dumps(out))

    assert out["outside_before"] == len(rows)
    assert out["doctor_before"] == f"; {len(rows)} stored vector(s) outside the active space (dense recall skips them)"
    assert not any(out["recall_before_hit"].values())
    assert out["reembed"]["reembedded"] == len(rows)
    assert out["outside_after"] == 0
    assert min(fidelity.values()) > 0.999
    assert all(rank == 1 for rank in dense_rank.values())
    assert out["doctor_after"] == "; 0 stored vector(s) outside the active space (dense recall skips them)"
    assert all(out["recall_after_hit"].values())
