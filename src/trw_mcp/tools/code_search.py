"""Pure callables for local code search and symbol lookup.

There is no ``mode`` parameter. One shipped with PRD-CORE-172 typed
``Literal["lexical", "semantic"]``, and its semantic branch read
``rank_semantic_chunks(query=query, chunks=(), embedder=None)`` -- a hardcoded
empty chunk collection and no embedder, so the member was registered, callable,
statically live, and structurally incapable of returning a result. 2.0.0 removes
it rather than implementing it (UF-031): search is lexical. The parameter is
GONE rather than narrowed to a one-member ``Literal``, because a knob with a
single accepted value is the same dead surface wearing a smaller type. ``mode``
is now refused by the tool's own input schema, which carries
``additionalProperties: false``.
"""

from __future__ import annotations

from fastmcp import FastMCP

from trw_mcp.code_index.search import lexical_search, response_to_dict, symbol_search
from trw_mcp.tools.telemetry import log_tool_call


def trw_code_search(
    repo_root: str,
    query: str,
    top_k: int = 10,
    path: str | None = None,
) -> dict[str, object]:
    """Search indexed code chunks and return capped, privacy-safe snippets."""

    return response_to_dict(lexical_search(repo_root, query=query, top_k=top_k, path=path))


def trw_code_symbol(
    repo_root: str,
    symbol: str,
    top_k: int = 10,
    path: str | None = None,
) -> dict[str, object]:
    """Look up indexed symbols with exact matches ranked before fuzzy matches."""

    return response_to_dict(symbol_search(repo_root, symbol=symbol, top_k=top_k, path=path))


def register_code_search_tools(server: FastMCP) -> None:
    """Register code-search MCP tools."""

    @server.tool(name="trw_code_search", output_schema=None)
    @log_tool_call
    def trw_code_search_tool(
        repo_root: str,
        query: str,
        top_k: int = 10,
        path: str | None = None,
    ) -> dict[str, object]:
        """Search local indexed code chunks by full-text/lexical query.

        Use when an agent has run ``trw_code_index_update`` and needs ranked code
        context without grepping the tree or reading full files.
        """

        return trw_code_search(repo_root=repo_root, query=query, top_k=top_k, path=path)

    @server.tool(name="trw_code_symbol", output_schema=None)
    @log_tool_call
    def trw_code_symbol_tool(
        repo_root: str,
        symbol: str,
        top_k: int = 10,
        path: str | None = None,
    ) -> dict[str, object]:
        """Find where a symbol is defined — function, class, or method — exact matches first.

        Use when an agent needs a definition's location from the local code
        index without scanning the tree or returning full file bodies.
        """

        return trw_code_symbol(repo_root=repo_root, symbol=symbol, top_k=top_k, path=path)


__all__ = [
    "register_code_search_tools",
    "trw_code_search",
    "trw_code_symbol",
]
