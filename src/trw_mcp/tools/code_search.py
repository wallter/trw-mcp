"""Pure callables for local code search and symbol lookup."""

from __future__ import annotations

from typing import Literal

from fastmcp import FastMCP

from trw_mcp.code_index.embeddings import rank_semantic_chunks
from trw_mcp.code_index.search import lexical_search, response_to_dict, symbol_search
from trw_mcp.tools.telemetry import log_tool_call


def trw_code_search(
    repo_root: str,
    query: str,
    mode: Literal["lexical", "semantic"] = "lexical",
    top_k: int = 10,
    path: str | None = None,
) -> dict[str, object]:
    """Search indexed code chunks and return capped, privacy-safe snippets."""

    if mode == "semantic":
        return response_to_dict(rank_semantic_chunks(query=query, chunks=(), embedder=None))
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
        mode: Literal["lexical", "semantic"] = "lexical",
        top_k: int = 10,
        path: str | None = None,
    ) -> dict[str, object]:
        """Search local indexed code chunks.

        Use after ``trw_code_index_update`` when an agent needs ranked code
        context without reading full files.
        """

        return trw_code_search(repo_root=repo_root, query=query, mode=mode, top_k=top_k, path=path)

    @server.tool(name="trw_code_symbol", output_schema=None)
    @log_tool_call
    def trw_code_symbol_tool(
        repo_root: str,
        symbol: str,
        top_k: int = 10,
        path: str | None = None,
    ) -> dict[str, object]:
        """Find local indexed symbols with exact matches ranked first."""

        return trw_code_symbol(repo_root=repo_root, symbol=symbol, top_k=top_k, path=path)


__all__ = [
    "register_code_search_tools",
    "trw_code_search",
    "trw_code_symbol",
]
