"""Withdraw what ``merge_codex_config`` added to a marker-managed ``.codex/config.toml`` (E2E-UNINSTALL-CODEX).

Belongs to the ``_subcommands_uninstall_config.py`` strip strategies (``codex-toml`` shape). Uninstall used to
remove only ``[mcp_servers.trw]``, so ``model_instructions_file`` kept naming the deleted ``INSTRUCTIONS.md`` and
``[skills]`` kept paths into the deleted ``.agents/skills`` (INC-012). Everything inside the TRW managed blocks is
TRW's merge of the user's own values plus TRW's, so this is that merge run backwards: TRW's server, the docs
server while it still equals TRW's default, TRW's instruction file and TRW's skill entries go; the user's other
servers, skill entries, features and fallback files stay. The user region between its markers is kept verbatim.
"""

from __future__ import annotations

import tomllib

from trw_mcp.bootstrap._codex import _codex_instruction_path, _docs_mcp_server_entry, _skill_paths
from trw_mcp.bootstrap._codex_toml import (
    MANAGED_ROOT_BEGIN,
    MANAGED_ROOT_END,
    MANAGED_TABLES_BEGIN,
    MANAGED_TABLES_END,
    _between,
    _toml_dumps,
    split_managed_block,
)

__all__ = ["strip_codex_managed"]

_CODEX_PREFIX = ".codex/"


def _trw_instruction_value() -> str:
    """The ``model_instructions_file`` value ``merge_codex_config`` writes (relative to ``.codex/``)."""
    return _codex_instruction_path().removeprefix(_CODEX_PREFIX)


def _unmerge(managed: dict[str, object]) -> dict[str, object]:
    """*managed* without what TRW contributed to it."""
    kept = dict(managed)
    servers = kept.get("mcp_servers")
    if isinstance(servers, dict):
        servers = {name: entry for name, entry in servers.items() if name != "trw"}
        if servers.get("openaiDeveloperDocs") == _docs_mcp_server_entry():
            del servers["openaiDeveloperDocs"]
        kept["mcp_servers"] = servers
    if kept.get("model_instructions_file") == _trw_instruction_value():
        del kept["model_instructions_file"]
    skills = kept.get("skills")
    if isinstance(skills, dict):
        ours = set(_skill_paths())
        config = skills.get("config")
        entries = (
            [e for e in config if not (isinstance(e, dict) and e.get("path") in ours)]
            if isinstance(config, list)
            else config
        )
        skills = {**skills, "config": entries} if entries else {k: v for k, v in skills.items() if k != "config"}
        kept["skills"] = skills
    return {key: value for key, value in kept.items() if value not in ({}, [], None)}


def strip_codex_managed(raw: str) -> tuple[bool, str, bool] | None:
    """``(changed, rendered, delete)`` for a marker-managed codex config, or ``None`` for an unmanaged one.

    Kept bare keys go ABOVE the user region and kept tables BELOW it: in TOML a bare key binds to the last
    table header above it, so the regenerated layout's order is the only one that reads back as written.
    A file left with nothing is deleted.
    """
    user_region = split_managed_block(raw)
    if user_region is None:
        return None
    # Only what sits INSIDE the managed blocks is TRW's merge. The user region is kept verbatim, so reading
    # it here too would re-emit a table the user added there (``[mcp_servers.mine]``) a second time below it:
    # a duplicate table codex cannot parse (lead review, W1-E2E-UNINSTALL-LEFTOVERS).
    managed_text = "\n".join(
        _between(raw, begin, end) or ""
        for begin, end in ((MANAGED_ROOT_BEGIN, MANAGED_ROOT_END), (MANAGED_TABLES_BEGIN, MANAGED_TABLES_END))
    )
    kept = _unmerge(tomllib.loads(managed_text))
    root = {key: value for key, value in kept.items() if not isinstance(value, dict)}
    tables: dict[str, object] = {key: value for key, value in kept.items() if isinstance(value, dict)}
    parts = [_toml_dumps(root) if root else "", user_region, _toml_dumps(tables) if tables else ""]
    text = "\n\n".join(part.strip("\n") for part in parts if part.strip())
    if not text:
        return True, "", True
    return True, text + "\n", False
