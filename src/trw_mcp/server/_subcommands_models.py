"""``trw-mcp models fetch`` -- the one explicit model download (PRD-CORE-302 W40).

Runtime model loads are cache-only; when a model is missing they fail or skip
with this command as the fix. It downloads the embedding model the daemon
loads (``MEMORY_EMBEDDING_MODEL``) and the recall re-ranker through ``trw_memory.embeddings.fetch_models``,
the same call the installer makes, so both land exactly the pinned files the
runtime reads.
"""

from __future__ import annotations

import argparse
import json
import sys

__all__ = ["run_models"]


def _fetch() -> dict[str, object]:
    from trw_mcp.state._retrieval_capability import daemon_embedding_model

    try:
        from trw_memory.embeddings import fetch_models
    except ImportError as exc:
        return {"status": "unavailable", "error": f"{exc}; install trw-memory[embeddings]"}
    try:
        fetched = fetch_models(embedding_model=daemon_embedding_model())
    except ImportError as exc:
        return {"status": "unavailable", "error": f"{exc}; install trw-memory[embeddings]"}
    except Exception as exc:  # justified: boundary, the download failure is the reported outcome
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    return {"status": "fetched", "models": fetched}


def run_models(args: argparse.Namespace) -> None:
    """Dispatch ``models <subcommand>``; a fetch that did not land exits 1."""
    if getattr(args, "models_command", None) != "fetch":
        print("Usage: trw-mcp models fetch [--json]", file=sys.stderr)
        sys.exit(2)
    answer = _fetch()
    if args.as_json:
        print(json.dumps(answer))
    elif isinstance(models := answer.get("models"), dict):
        for model, revision in models.items():
            print(f"models fetch: {model} @ {revision}")
    else:
        print(f"models fetch: {answer['status']}: {answer['error']}", file=sys.stderr)
    sys.exit(0 if answer["status"] == "fetched" else 1)
