"""TOML parsing/serialization helpers — extracted from _codex.py for module-size compliance.

Belongs to the ``_codex.py`` facade. Re-exported there for back-compat with
internal callers (``_codex.py`` is the only consumer; no external imports).

Self-contained TOML helpers for the Codex bootstrap path:
- ``_parse_codex_toml`` — read a Codex TOML config into a typed dict
- ``_toml_key`` — quote a key when required by TOML grammar
- ``_toml_value`` — render a TOML literal (bool, str, int, float, list)
- ``_toml_dumps`` — serialize a dict-of-dicts into TOML text
"""

from __future__ import annotations

import json
import re
import sys
from typing import cast

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import structlog

from trw_mcp.models.typed_dicts import BootstrapFileResult, CodexConfigDict

logger = structlog.get_logger(__name__)

#: Repo-relative path of the generated file, used in the advisory messages below.
CODEX_CONFIG_PATH = ".codex/config.toml"


def _parse_codex_toml(content: str) -> CodexConfigDict:
    """Parse Codex TOML config into a dict."""
    return cast("CodexConfigDict", tomllib.loads(content))


def _toml_key(key: str) -> str:
    """Render a TOML key, quoting only when required."""
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    if key and all(char in allowed for char in key):
        return key
    return json.dumps(key)


def _toml_value(value: object) -> str:
    """Render a TOML literal for the subset used by Codex bootstrap."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        # Empty list FIRST: ``all(...)`` over an empty sequence is vacuously
        # True, which used to route [] into the inline-table branch below and
        # emit the invalid TOML ``[\n  ,\n]``. Latent until the trw entry's
        # ``args`` became empty (2026-07-27 --debug removal), at which point
        # every generated .codex/config.toml failed to parse.
        if not value:
            return "[]"
        if all(isinstance(item, dict) for item in value):
            inline_tables: list[str] = []
            for item in value:
                dict_item = cast("dict[str, object]", item)
                parts = [f"{_toml_key(k)} = {_toml_value(v)}" for k, v in dict_item.items()]
                inline_tables.append("{ " + ", ".join(parts) + " }")
            return "[\n  " + ",\n  ".join(inline_tables) + ",\n]"
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"Unsupported TOML value type: {type(value).__name__}")


def _toml_dumps(data: dict[str, object]) -> str:
    """Serialize the Codex config structure to TOML without external deps."""
    lines: list[str] = []

    def emit_table(table: dict[str, object], prefix: str | None = None) -> None:
        scalar_items: list[tuple[str, object]] = []
        child_tables: list[tuple[str, dict[str, object]]] = []

        for key, value in table.items():
            if isinstance(value, dict):
                child_tables.append((key, cast("dict[str, object]", value)))
            else:
                scalar_items.append((key, value))

        if prefix is not None:
            lines.append(f"[{prefix}]")
        for key, value in scalar_items:
            lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
        if prefix is not None and (scalar_items or child_tables):
            lines.append("")

        for key, child in child_tables:
            child_prefix = _toml_key(key) if prefix is None else f"{prefix}.{_toml_key(key)}"
            emit_table(child, child_prefix)

    emit_table(data)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


# ── Managed regions (PRD-CORE-277-FR07) ──────────────────────────────────────
#
# `.codex/config.toml` is co-authored: codex exposes no other place for
# per-project MCP settings, so TRW must rewrite part of the file without
# destroying the rest. The layout is THREE regions, and the order is forced by
# TOML grammar rather than taste: a comment does NOT close a table, so a bare
# key binds to the last table header above it. With one managed block on top,
# a user's `model = "..."` written below it would silently become
# `skills.model` -- and every managed key would still read back correctly, so a
# managed-side-only validation would pass.
#
#   1. managed ROOT keys   -- bare keys only, no table header
#   2. the USER region     -- their root keys AND their tables
#   3. managed TABLES      -- opens with [features], so it captures nothing above
#
# Rewriting regions 1 and 3 leaves region 2 byte-identical, which is the whole
# point: `update-project` used to delete hand-added [mcp_servers.trw.env] and
# [mcp_servers.trw.tools.*] tables on every run.

MANAGED_ROOT_BEGIN = "# >>> TRW MANAGED BLOCK (root keys) — regenerated by trw-mcp; edits here are lost >>>"
MANAGED_ROOT_END = "# <<< TRW MANAGED BLOCK (root keys) <<<"
USER_REGION_BEGIN = "# --- your settings below — TRW never rewrites this region ---"
USER_REGION_END = "# --- end of your settings ---"
MANAGED_TABLES_BEGIN = "# >>> TRW MANAGED BLOCK (tables) — regenerated by trw-mcp; edits here are lost >>>"
MANAGED_TABLES_END = "# <<< TRW MANAGED BLOCK (tables) <<<"


def _between(text: str, begin: str, end: str) -> str | None:
    """Return the text between two marker LINES, or None if not both present.

    Line-anchored comparison, never a substring scan: a marker quoted inside a
    string value must not be mistaken for the real delimiter.
    """
    lines = text.splitlines()
    try:
        start = lines.index(begin)
        stop = lines.index(end, start + 1)
    except ValueError:  # trw-fail-silent-allow: a missing marker IS the answer — an unmanaged file, reported as None
        return None
    return "\n".join(lines[start + 1 : stop])


def split_managed_block(text: str) -> str | None:
    """Return the USER region of *text*, or ``None`` when it carries no markers.

    ``None`` means "not managed yet" and triggers the one-time migration, which
    is different from ``""`` (a managed file whose user region is empty).
    """
    return _between(text, USER_REGION_BEGIN, USER_REGION_END)


_TABLE_HEADER_RE = re.compile(r"^\[\[?([^\].]+)")


def strip_managed_from_user_region(user_region: str, managed_keys: frozenset[str]) -> tuple[str, list[str]]:
    """Remove TRW-owned top-level keys from the user region; report what moved.

    A user who hand-added ``[mcp_servers.trw.env]`` wrote it BELOW the markers,
    which is the natural place -- but ``mcp_servers`` is a managed table, so
    leaving the text in place would emit that table twice and produce a file
    codex cannot parse at all. The merge has already absorbed the value into the
    managed entry (TRW preserves user sub-keys of ``mcp_servers.trw``), so the
    correct move is to drop the now-duplicated text and SAY that it moved.

    Returns the cleaned region and the sorted top-level keys removed from it.
    """
    kept: list[str] = []
    removed: set[str] = set()
    dropping = False
    for line in user_region.splitlines():
        stripped = line.strip()
        header = _TABLE_HEADER_RE.match(stripped)
        if header:
            root = header.group(1).strip().strip('"').strip("'")
            dropping = root in managed_keys
            if dropping:
                removed.add(root)
                continue
        elif dropping:
            continue
        elif "=" in stripped and not stripped.startswith("#"):
            root = stripped.split("=", 1)[0].strip().strip('"').strip("'").split(".")[0]
            if root in managed_keys:
                removed.add(root)
                continue
        kept.append(line)
    return "\n".join(kept).strip("\n"), sorted(removed)


def compose_managed_block(*, managed_root: str, user_region: str, managed_tables: str) -> str:
    """Assemble the three regions into one file."""
    parts = [
        MANAGED_ROOT_BEGIN,
        managed_root.strip("\n"),
        MANAGED_ROOT_END,
        "",
        USER_REGION_BEGIN,
        user_region.strip("\n"),
        USER_REGION_END,
        "",
        MANAGED_TABLES_BEGIN,
        managed_tables.strip("\n"),
        MANAGED_TABLES_END,
    ]
    return "\n".join(part for part in parts if part is not None) + "\n"


def composition_is_faithful(composed: str, *, managed: dict[str, object], user_region: str) -> bool:
    """True iff *composed* preserves BOTH sides' meaning.

    Managed-side only would be insufficient (see the layout note above), so the
    user region is parsed STANDALONE and every one of its top-level keys must
    read back with the same value from the composed document. A False here is not
    a crash: the caller falls back to the whole-file merge, which loses comments
    but never moves a user's key into a TRW table.
    """
    try:
        whole = tomllib.loads(composed)
        user_only = tomllib.loads(user_region)
    except tomllib.TOMLDecodeError:
        # Not swallowed: False is the degraded signal the caller acts on (it
        # keeps the user's bytes and says the region could not be verified).
        logger.info(
            "codex_config_composition_unparseable",
            path=CODEX_CONFIG_PATH,
            outcome="user_region_unverified",
            exc_info=True,
        )
        return False
    for key, value in managed.items():
        if whole.get(key) != value:
            return False
    return all(whole.get(key) == value for key, value in user_only.items())


#: Top-level keys ``merge_codex_config`` writes. Everything else in the file is
#: the user's and is carried through the regeneration untouched.
_TRW_MANAGED_CONFIG_KEYS: frozenset[str] = frozenset(
    {"features", "mcp_servers", "skills", "model_instructions_file", "project_doc_fallback_filenames"}
)


def render_config_text(
    merged: CodexConfigDict,
    *,
    user_region: str | None,
    result: BootstrapFileResult,
) -> str:
    """Render the merged config as three regions, or fall back to a flat dump.

    *user_region* is the text below the user marker (``None`` on a marker-less
    file, which is the one-time migration: the user's own top-level keys are
    emitted INTO the user region rather than absorbed into the managed block,
    because anything inside the block is overwritten on the next run).
    """
    data = cast("dict[str, object]", merged)
    managed = {key: value for key, value in data.items() if key in _TRW_MANAGED_CONFIG_KEYS}
    leftover = {key: value for key, value in data.items() if key not in _TRW_MANAGED_CONFIG_KEYS}
    managed_root = _toml_dumps({k: v for k, v in managed.items() if not isinstance(v, dict)}) if managed else ""
    managed_tables = _toml_dumps({k: v for k, v in managed.items() if isinstance(v, dict)}) if managed else ""
    region = user_region if user_region is not None else (_toml_dumps(leftover) if leftover else "")
    region, moved = strip_managed_from_user_region(region, _TRW_MANAGED_CONFIG_KEYS)
    if moved:
        # ``info``, not ``errors``: nothing failed and nothing was lost. The
        # installer prints errors as failures, and a truthful advisory reported
        # there would train an operator to ignore the field that matters.
        result.setdefault("info", []).append(
            f"{CODEX_CONFIG_PATH}: moved {', '.join(moved)} out of the user region into the "
            "TRW-managed block, which preserves your sub-keys (a duplicate table would not parse)."
        )
        logger.info("codex_config_user_region_key_moved", keys=moved)
    composed = compose_managed_block(
        managed_root=managed_root,
        user_region=region,
        managed_tables=managed_tables,
    )
    if not composition_is_faithful(composed, managed=managed, user_region=region):
        # The composed file does not read back as intended -- in practice, a user
        # region that is not valid TOML on its own. Their bytes are still written:
        # the alternative (a flat re-dump of the parsed config) would DELETE the
        # text they typed, and a file the user broke is theirs to fix. Say so.
        result.setdefault("info", []).append(
            f"{CODEX_CONFIG_PATH}: your region below the marker did not read back as valid TOML; "
            "it was preserved as written and NOT rewritten by TRW."
        )
        logger.warning("codex_config_user_region_unverified", path=CODEX_CONFIG_PATH)
    return composed
