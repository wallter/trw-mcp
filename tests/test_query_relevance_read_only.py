"""Request-local lexical documents are not persisted memory identities."""

from copy import deepcopy


def test_query_relevance_returns_scores_without_writing_entries(monkeypatch, config) -> None:
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    from trw_mcp.scoring import _utils
    from trw_mcp.scoring._query_relevance import query_relevance

    def unexpected_write(*args, **kwargs):
        raise AssertionError("query ranking must not persist request-local documents")

    monkeypatch.setattr(SQLiteBackend, "store", unexpected_write)
    monkeypatch.setattr(SQLiteBackend, "update", unexpected_write)
    monkeypatch.setattr(_utils, "get_config", lambda: config)
    rows = [
        {"id": "same", "summary": "router dns outage", "detail": "repair resolver", "tags": ["network"]},
        {"id": "same", "summary": "picnic menu", "detail": "fresh fruit", "tags": ["food"]},
    ]
    original = deepcopy(rows)
    scores = query_relevance(rows, ["router", "dns"])
    assert len(scores) == 2
    assert 0 <= scores[1] < scores[0] <= 1
    assert rows == original
