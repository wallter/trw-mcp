"""Pure callables behind ``trw_code(mode="search")`` and ``trw_code(mode="symbol")``.

Registration lives in ``tools/code.py`` (PRD-CORE-300-FR12). Search is lexical:
PRD-CORE-172 once shipped a ``semantic`` member whose branch read a hardcoded
empty chunk collection, so it could never return a result; 2.0.0 removed it
(UF-031) rather than implementing it.
"""

from __future__ import annotations

from trw_mcp.code_index.bounds import CodeIndexBounds
from trw_mcp.code_index.search import lexical_search, response_to_dict, symbol_search


def _bounds() -> CodeIndexBounds:
    from trw_mcp.models.config import get_config

    return get_config().code_index_bounds


def code_search(
    repo_root: str,
    query: str,
    top_k: int = 10,
    path: str | None = None,
) -> dict[str, object]:
    """Search indexed code chunks and return capped, privacy-safe snippets."""

    return response_to_dict(lexical_search(repo_root, query=query, top_k=top_k, path=path, bounds=_bounds()))


def code_symbol(
    repo_root: str,
    query: str,
    top_k: int = 10,
    path: str | None = None,
) -> dict[str, object]:
    """Look up indexed symbols named *query*, exact matches ranked before fuzzy ones."""

    return response_to_dict(symbol_search(repo_root, symbol=query, top_k=top_k, path=path, bounds=_bounds()))


__all__ = ["code_search", "code_symbol"]
