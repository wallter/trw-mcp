"""Lifecycle CLI subcommand handlers — extracted from _subcommands.py for module-size compliance.

Belongs to the ``_subcommands.py`` facade. Re-exported there for back-compat
with test imports (``test_uninstall.py``, ``test_cli_auth_subcommand.py``).

Two handlers:
- ``_run_uninstall`` — remove TRW files from a project (uninstall subcommand)
- ``_run_auth`` — login/logout/status auth subcommand dispatch
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import structlog

# PRD-SEC-006 FR07: TRW-managed marker block pairs for SHARED files. Uninstall
# strips ONLY the content between these markers (preserving user content) rather
# than deleting the file wholesale. Each shared instruction file uses one pair;
# the bootstrap layer is the source of these literals (kept in sync there).
_MANAGED_BLOCK_MARKERS: tuple[tuple[str, str], ...] = (
    ("<!-- trw:start -->", "<!-- trw:end -->"),
    ("<!-- trw:gemini:start -->", "<!-- trw:gemini:end -->"),
    ("<!-- trw:copilot:start -->", "<!-- trw:copilot:end -->"),
    ("<!-- trw:antigravity:start -->", "<!-- trw:antigravity:end -->"),
)

# TRW server entry key written into merged client config files. JSON
# (.gemini/settings.json) nests it under ``mcpServers``; TOML
# (.codex/config.toml) under ``mcp_servers``.
_TRW_SERVER_KEY = "trw"
_JSON_MCP_KEY = "mcpServers"
_TOML_MCP_KEY = "mcp_servers"


def _runtime_logger() -> Any:
    """Return a fresh logger so structlog test capture sees late-bound events."""
    return structlog.get_logger(__name__)


def _has_unbalanced_marker(text: str) -> bool:
    """True when any start marker appears on its own line with no matching end.

    Line-anchored: a marker mentioned inside prose (substring) does not count.
    Deleting to EOF on an unbalanced marker would destroy user content, so the
    caller leaves the file untouched and warns when this returns True.
    """
    lines = [ln.strip() for ln in text.splitlines()]
    return any(start in lines and end not in lines for start, end in _MANAGED_BLOCK_MARKERS)


def _strip_managed_blocks(text: str) -> str:
    """Remove every line-anchored TRW-managed marker block from *text*.

    A block is only stripped when BOTH its start and end markers appear as
    their own (whitespace-stripped) lines — never as substrings inside prose
    (an earlier substring match risked deleting user content; see the
    ``index_sync._find_marker_line`` precedent). An unbalanced start marker
    (start present, end missing) is left untouched here so the caller can warn
    rather than delete to EOF. Surrounding user lines are preserved and blank
    runs left by removal are collapsed.
    """
    if _has_unbalanced_marker(text):
        # Unbalanced — refuse to strip; the caller decides (warn + preserve).
        return text

    marker_pairs = dict(_MANAGED_BLOCK_MARKERS)
    end_markers = set(marker_pairs.values())
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    skipping_end: str | None = None
    for line in lines:
        stripped = line.strip()
        if skipping_end is not None:
            if stripped == skipping_end:
                skipping_end = None
            continue
        if stripped in marker_pairs:
            skipping_end = marker_pairs[stripped]
            continue
        if stripped in end_markers:
            # Defensive: orphan end line with no preceding start — drop it.
            continue
        out.append(line)
    result = "".join(out)
    # Collapse 3+ consecutive newlines created by removal into a single blank.
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")
    return result


def _remove_managed_block_file(path: Path, dry_run: bool) -> str | None:
    """Strip TRW blocks from a shared file; return a status word or None.

    Returns ``"stripped"`` when the TRW block was removed (file preserved),
    ``"removed"`` when stripping left the file empty (file deleted), or ``None``
    when the file has no TRW block (left untouched). On ``dry_run`` the same
    classification is returned without mutating the file.
    """
    try:
        original = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if _has_unbalanced_marker(original):
        # A start marker with no matching end. Stripping to EOF would destroy
        # user content; leave the file untouched and surface the anomaly.
        _runtime_logger().warning(
            "uninstall_marker_unbalanced",
            path=str(path),
            action="left_untouched",
        )
        return None
    stripped = _strip_managed_blocks(original)
    if stripped == original:
        return None  # no TRW block present -- leave the user's file alone
    if dry_run:
        return "removed" if not stripped.strip() else "stripped"
    if not stripped.strip():
        path.unlink()
        return "removed"
    path.write_text(stripped, encoding="utf-8")
    return "stripped"


def _strip_trw_from_merged_config(path: Path, dry_run: bool) -> str | None:
    """Strip ONLY the TRW server entry from a merged client config file.

    sec-006: ``.gemini/settings.json`` (JSON) and ``.codex/config.toml`` (TOML)
    are shared config files that may carry user-owned settings/servers. They
    MUST NOT be deleted wholesale on uninstall — we parse the file, drop the
    ``trw`` server entry from the mcp-servers map, and write the rest back.

    Returns ``"stripped"`` when the TRW entry was removed, ``None`` when the
    file has no TRW entry (left untouched), or ``"skipped"`` when the file
    cannot be parsed (left untouched + warned). On ``dry_run`` the same
    classification is returned without mutating the file.
    """
    suffix = path.suffix.lower()
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        if suffix == ".json":
            changed, rendered = _strip_trw_json(raw)
        elif suffix == ".toml":
            changed, rendered = _strip_trw_toml(raw)
        else:
            return None
    except (ValueError, TypeError) as exc:
        _runtime_logger().warning(
            "uninstall_merged_config_unparseable",
            path=str(path),
            error=type(exc).__name__,
            action="left_untouched",
        )
        return "skipped"
    if not changed:
        return None
    if dry_run:
        return "stripped"
    path.write_text(rendered, encoding="utf-8")
    return "stripped"


def _strip_trw_json(raw: str) -> tuple[bool, str]:
    """Remove ``mcpServers.trw`` from JSON text; return (changed, rendered)."""
    import json

    data = json.loads(raw)
    if not isinstance(data, dict):
        return False, raw
    servers = data.get(_JSON_MCP_KEY)
    if not isinstance(servers, dict) or _TRW_SERVER_KEY not in servers:
        return False, raw
    del servers[_TRW_SERVER_KEY]
    if not servers:
        # Last server was ours — drop the now-empty container key too.
        del data[_JSON_MCP_KEY]
    return True, json.dumps(data, indent=2) + "\n"


def _strip_trw_toml(raw: str) -> tuple[bool, str]:
    """Remove the ``[mcp_servers.trw]`` table from TOML text; return (changed, rendered).

    Reuses the codex TOML helpers (round-trip safe) so user tables/keys and
    comments are preserved structurally.
    """
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover - Python <3.11 fallback
        import tomli as tomllib

    from trw_mcp.bootstrap._codex_toml import _toml_dumps

    data = tomllib.loads(raw)
    servers = data.get(_TOML_MCP_KEY)
    if not isinstance(servers, dict) or _TRW_SERVER_KEY not in servers:
        return False, raw
    del servers[_TRW_SERVER_KEY]
    if not servers:
        del data[_TOML_MCP_KEY]
    return True, _toml_dumps(data)


# Subpaths of a project ``.trw`` dir that hold the durable learning corpus.
# ``--keep-memory`` preserves these; the blast-radius warning is gated on them.
# ``memory.db`` is the authoritative SQLite store (a FILE, not the memory/ dir),
# so it MUST be preserved alongside the learning entry files.
_MEMORY_SUBPATHS: tuple[str, ...] = ("memory", "memory.db", "learnings")


def _count_learnings(trw_dir: Path) -> int:
    """Best-effort count of learning entry files under ``.trw/learnings``.

    Counts ``.yaml`` files under ``learnings/`` (and its ``entries/`` subdir),
    excluding the ``index.yaml`` seed. A missing directory yields 0. This is a
    rough blast-radius figure for the destructive-uninstall warning, not an
    exact corpus size (the authoritative store is ``memory.db``).
    """
    learnings = trw_dir / "learnings"
    if not learnings.is_dir():
        return 0
    return sum(1 for p in learnings.rglob("*.yaml") if p.is_file() and p.name != "index.yaml")


def _trw_corpus_blast_radius(trw_dir: Path) -> tuple[bool, int]:
    """Return ``(has_corpus, learning_count)`` for a project ``.trw`` dir.

    ``has_corpus`` is True when ``memory.db`` exists OR any learning entry
    files are present — i.e. removing this dir would permanently destroy the
    accumulated learning corpus. Used to gate the destructive-uninstall
    warning + export nudge.
    """
    has_db = (trw_dir / "memory.db").is_file()
    count = _count_learnings(trw_dir)
    return (has_db or count > 0), count


def _keep_memory_in_dir(trw_dir: Path, target: Path) -> int:
    """Remove everything under *trw_dir* EXCEPT memory/ and learnings/.

    Implements ``--keep-memory``: the durable learning corpus
    (``.trw/memory`` + ``.trw/learnings``) is preserved while all other
    session/config state is removed. Returns the number of top-level entries
    removed. The ``.trw`` dir itself is preserved (it still holds the corpus).
    """
    import shutil

    removed = 0
    preserved = {trw_dir / name for name in _MEMORY_SUBPATHS}
    for child in sorted(trw_dir.iterdir()):
        # Preserve the corpus dirs/files plus SQLite sidecars (memory.db-wal /
        # memory.db-shm) so the kept DB reopens cleanly.
        if child in preserved or child.name.startswith("memory.db"):
            continue
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
            removed += 1
            print(f"  Removed: {_display(child, target)}")
        except OSError as exc:
            print(f"  Error removing {_display(child, target)}: {exc}")
    return removed


def _run_uninstall(args: argparse.Namespace) -> None:
    """Handle the ``uninstall`` subcommand -- remove TRW files from a project.

    Registry-driven (PRD-SEC-006 FR07): the set of surfaces is derived from the
    client-profile registry manifest (all 9 profiles + framework core), so
    every profile is cleaned, not just claude-code. Shared files (CLAUDE.md,
    AGENTS.md, GEMINI.md, settings.json, copilot-instructions) have only their
    TRW-managed marker block removed; only artifacts TRW created are touched.
    With ``--user-tier`` the machine-local ``~/.trw`` store is also removed.
    """
    import shutil

    from trw_mcp.client_profiles.catalog import uninstall_surfaces

    target = Path(getattr(args, "target_dir", ".")).resolve()
    dry_run: bool = getattr(args, "dry_run", False)
    yes: bool = getattr(args, "yes", False)
    user_tier: bool = getattr(args, "user_tier", False)
    keep_memory: bool = getattr(args, "keep_memory", False)

    # Blast-radius detection for the project .trw learning corpus. Removing
    # .trw wholesale permanently destroys memory.db + all learnings, so we warn
    # explicitly + nudge an export-first when a corpus is present.
    project_trw = (target / ".trw").resolve()
    has_corpus, learning_count = _trw_corpus_blast_radius(project_trw) if project_trw.is_dir() else (False, 0)

    plain_paths: list[Path] = []
    managed_paths: list[Path] = []
    merged_config_paths: list[Path] = []
    for surface in uninstall_surfaces():
        path = (target / surface.relpath).resolve()
        if not path.exists():
            continue
        if surface.merged_config:
            merged_config_paths.append(path)
        elif surface.managed_block:
            managed_paths.append(path)
        else:
            plain_paths.append(path)

    user_trw = (Path.home() / ".trw").resolve()
    remove_user_trw = user_tier and user_trw.exists() and user_trw not in plain_paths

    if not plain_paths and not managed_paths and not merged_config_paths and not remove_user_trw:
        print("  No TRW files found in this project.")
        return

    print(f"\n  TRW files found in {target}:\n")
    for p in plain_paths:
        kind = "dir " if p.is_dir() else "file"
        size = ""
        if p.is_dir():
            count = sum(1 for _ in p.rglob("*") if _.is_file())
            size = f" ({count} files)"
        note = ""
        if p == project_trw and keep_memory and has_corpus:
            note = " — memory/ + learnings/ PRESERVED (--keep-memory)"
        print(f"    {kind}  {_display(p, target)}{size}{note}")
    for p in managed_paths:
        print(f"    block {_display(p, target)} (TRW-managed section)")
    for p in merged_config_paths:
        print(f"    entry {_display(p, target)} (TRW server entry only)")
    if remove_user_trw:
        print(f"    dir   {user_trw} (user-tier)")

    # Destructive blast-radius warning: removing project .trw without
    # --keep-memory permanently deletes memory.db + every learning. TRW's whole
    # value is durable learnings, so name the blast radius + nudge export-first.
    corpus_at_risk = has_corpus and not keep_memory
    if corpus_at_risk:
        _print_corpus_warning(project_trw, learning_count, target)

    if dry_run:
        for p in managed_paths:
            _remove_managed_block_file(p, dry_run=True)
        for p in merged_config_paths:
            _strip_trw_from_merged_config(p, dry_run=True)
        print("\n  --dry-run: no files removed.")
        return

    if not yes:
        print()
        prompt = (
            "  Permanently delete the learning corpus? [y/N] " if corpus_at_risk else "  Remove these files? [y/N] "
        )
        confirm = input(prompt).strip().lower()
        if confirm not in ("y", "yes"):
            print("  Aborted.")
            return

    removed = 0
    for p in plain_paths:
        # --keep-memory: preserve the learning corpus inside project .trw while
        # removing all other session/config state under it.
        if p == project_trw and keep_memory and has_corpus:
            removed += _keep_memory_in_dir(p, target)
            print(f"  Kept: {_display(p, target)}/memory + learnings (--keep-memory)")
            continue
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed += 1
            print(f"  Removed: {_display(p, target)}")
        except OSError as exc:
            print(f"  Error removing {_display(p, target)}: {exc}")

    for p in managed_paths:
        try:
            status = _remove_managed_block_file(p, dry_run=False)
        except OSError as exc:
            print(f"  Error updating {_display(p, target)}: {exc}")
            continue
        if status == "removed":
            removed += 1
            print(f"  Removed: {_display(p, target)} (TRW-only file)")
        elif status == "stripped":
            removed += 1
            print(f"  Cleaned: {_display(p, target)} (removed TRW section)")

    for p in merged_config_paths:
        try:
            status = _strip_trw_from_merged_config(p, dry_run=False)
        except OSError as exc:
            print(f"  Error updating {_display(p, target)}: {exc}")
            continue
        if status == "stripped":
            removed += 1
            print(f"  Cleaned: {_display(p, target)} (removed TRW server entry)")
        elif status == "skipped":
            print(f"  Preserved: {_display(p, target)} (unparseable; left untouched)")

    if remove_user_trw:
        try:
            shutil.rmtree(user_trw)
            removed += 1
            print(f"  Removed: {user_trw} (user-tier)")
        except OSError as exc:
            print(f"  Error removing {user_trw}: {exc}")

    print(f"\n  Done. Removed {removed} item(s).")
    if not user_tier and user_trw.exists():
        print("  Note: ~/.trw (user-tier store) preserved. Re-run with --user-tier to remove it.")
    print("  To uninstall the package itself: pip uninstall trw-mcp trw-memory")


def _print_corpus_warning(trw_dir: Path, learning_count: int, target: Path) -> None:
    """Print the destructive-uninstall blast-radius warning + export nudge.

    Names exactly what is about to be permanently destroyed (memory.db + the
    learning count) and nudges an export-first, since the learning corpus is
    TRW's core durable value and cannot be recovered after rmtree.
    """
    has_db = (trw_dir / "memory.db").is_file()
    pieces: list[str] = []
    if has_db:
        pieces.append("memory.db")
    if learning_count > 0:
        pieces.append(f"{learning_count} learning(s)")
    blast = " and ".join(pieces) if pieces else "the learning corpus"
    rel = _display(trw_dir, target)
    print()
    print("  WARNING: this permanently deletes your learning corpus.")
    print(f"    {rel} contains {blast} — removing it CANNOT be undone.")
    print("    Export first:  trw-mcp export --scope learnings --output learnings.json")
    print("    Or keep it:    re-run with --keep-memory to preserve memory/ + learnings/.")


def _display(path: Path, target: Path) -> str:
    """Render *path* relative to *target* when possible, else absolute."""
    try:
        return str(path.relative_to(target))
    except ValueError:
        return str(path)


def _run_auth(args: argparse.Namespace) -> None:
    """Handle the ``auth`` subcommand (login/logout/status)."""
    from trw_mcp.cli.auth import run_auth_login, run_auth_logout, run_auth_status

    config_path = Path.cwd() / ".trw" / "config.yaml"
    api_url = getattr(args, "api_url", None) or "https://api.trwframework.com"

    auth_cmd = getattr(args, "auth_command", None)
    if auth_cmd == "login":
        sys.exit(run_auth_login(api_url, config_path))
    elif auth_cmd == "logout":
        sys.exit(run_auth_logout(config_path))
    elif auth_cmd == "status":
        sys.exit(run_auth_status(config_path, api_url))
    else:
        # No auth subcommand: show help
        print("Usage: trw-mcp auth {login|logout|status}")
        print()
        print("Commands:")
        print("  login   Authenticate via device authorization flow")
        print("  logout  Remove stored API key")
        print("  status  Show current authentication status")
        sys.exit(0)
