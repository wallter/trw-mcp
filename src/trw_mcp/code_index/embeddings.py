"""Optional embedding hook for code search.

The base package intentionally does not import model libraries or download
models. Semantic search callers must provide an embedder object; otherwise this
module returns a structured dependency-missing response.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from trw_mcp.code_index.chunking import CodeChunk
from trw_mcp.code_index.search import CodeSearchResponse


class EmbeddingDependencyStatus(BaseModel):
    """Structured optional-dependency status for semantic code search."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    dependency_available: bool
    provider: str
    remediation: str


class CodeEmbedder(Protocol):
    """Minimal embedder protocol supplied by optional integrations."""

    def embed_query(self, text: str) -> tuple[float, ...]:
        """Embed a query locally without network access or downloads."""


def embedding_dependency_status() -> EmbeddingDependencyStatus:
    """Return base-install status without importing optional model packages."""

    return EmbeddingDependencyStatus(
        dependency_available=False,
        provider="local-optional",
        remediation="Install the optional code-search embedding extra and configure a local embedder.",
    )


def rank_semantic_chunks(
    *,
    query: str,
    chunks: tuple[CodeChunk, ...],
    embedder: CodeEmbedder | None,
) -> CodeSearchResponse:
    """Semantic ranking hook that fails closed when no optional embedder is supplied."""

    if embedder is None:
        status = embedding_dependency_status()
        return CodeSearchResponse(
            status="failed",
            mode="semantic",
            query=query,
            results=(),
            error_code="dependency_missing",
            error="semantic code search requires an optional local embedder",
            remediation=f"{status.remediation} No model download is attempted by the base install.",
        )

    embedder.embed_query(query)
    return CodeSearchResponse(status="ok", mode="semantic", query=query, results=())


__all__ = [
    "CodeEmbedder",
    "EmbeddingDependencyStatus",
    "embedding_dependency_status",
    "rank_semantic_chunks",
]
