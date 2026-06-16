from __future__ import annotations

from trw_mcp.code_index.embeddings import EmbeddingDependencyStatus, rank_semantic_chunks
from trw_mcp.code_index.search import CodeSearchResponse


def test_semantic_search_without_embedder_fails_closed_with_remediation() -> None:
    response = rank_semantic_chunks(query="meaning", chunks=(), embedder=None)

    assert isinstance(response, CodeSearchResponse)
    assert response.status == "failed"
    assert response.error_code == "dependency_missing"
    assert "optional code-search embedding extra" in response.remediation
    assert response.results == ()


class _FakeEmbedder:
    def embed_query(self, text: str) -> tuple[float, ...]:
        assert text == "meaning"
        return (1.0, 0.0)


def test_embedding_dependency_status_is_structured_and_does_not_download_models() -> None:
    status = EmbeddingDependencyStatus(
        dependency_available=False, provider="local-optional", remediation="install extras"
    )

    assert status.dependency_available is False
    assert status.provider == "local-optional"
    assert status.remediation == "install extras"


def test_semantic_hook_with_embedder_returns_empty_ok_response_for_no_chunks() -> None:
    response = rank_semantic_chunks(query="meaning", chunks=(), embedder=_FakeEmbedder())

    assert response.status == "ok"
    assert response.results == ()
