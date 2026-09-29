"""Pure callable behind ``trw_code(mode="symbol")``.

Registration lives in ``tools/code.py`` (PRD-CORE-300-FR12). ``trw_code(mode="search")``
(full-text lexical search) was retired in 8.0 in favor of `rg`/`grep` and the
`trw-distill` CLI; the ``code_search()`` wrapper that used to live here was
removed with it. ``code_index.search.lexical_search`` (the engine it wrapped)
stays: it shares its store-opening, bounds and security-invariant plumbing
with ``symbol_search`` below and is exercised directly by the code-index test
suite, so retiring it is a separate, lower-priority follow-up (it currently has
no other production caller). Symbol lookup still reads the same local index
(``trw-mcp code index``).
"""

from __future__ import annotations

from trw_mcp.code_index.bounds import CodeIndexBounds
from trw_mcp.code_index.search import response_to_dict, symbol_search


def _bounds() -> CodeIndexBounds:
    from trw_mcp.models.config import get_config

    return get_config().code_index_bounds


def code_symbol(
    repo_root: str,
    query: str,
    top_k: int = 10,
    path: str | None = None,
) -> dict[str, object]:
    """Look up indexed symbols named *query*, exact matches ranked before fuzzy ones."""

    return response_to_dict(symbol_search(repo_root, symbol=query, top_k=top_k, path=path, bounds=_bounds()))


__all__ = ["code_symbol"]
