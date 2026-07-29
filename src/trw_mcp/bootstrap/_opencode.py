"""OpenCode-specific bootstrap configuration.

FR11: OpenCode Bootstrap Configuration (PRD-CORE-074)
FR16: opencode.json Smart Merge (PRD-CORE-074)
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import cast

import structlog

from trw_mcp.channels.opencode._shared_lock import ChannelLockSkip, agents_md_lock
from trw_mcp.models.typed_dicts._opencode import (
    OpencodeConfig,
    OpencodeServerEntry,
    OpencodeTemplateDict,
)

from ._file_ops import _new_result, has_marker, replace_marker_region
from ._opencode_instructions import (
    detect_model_family as detect_model_family,
)
from ._opencode_instructions import (
    generate_codex_instructions as generate_codex_instructions,
)
from ._opencode_instructions import (
    generate_opencode_instructions as generate_opencode_instructions,
)
from ._utils import _DATA_DIR

logger = structlog.get_logger(__name__)

_TRW_START_MARKER = "<!-- trw:start -->"
_TRW_END_MARKER = "<!-- trw:end -->"
_TRW_HEADER = "<!-- TRW AUTO-GENERATED — do not edit between markers -->"

#: The TRW-owned instruction file opencode must be told to load. Single source
#: for both the fresh-install seed and the merge path, so the two cannot drift
#: into referencing different files (PRD-CORE-240-FR05).
_OPENCODE_INSTRUCTIONS_ENTRY = ".opencode/INSTRUCTIONS.md"

_DEFAULT_PERMISSIONS: dict[str, str] = {"bash": "ask", "write": "ask", "edit": "ask"}

_OPENCODE_DATA_DIR = _DATA_DIR / "opencode"
_OPENCODE_COMMANDS_DIR = _OPENCODE_DATA_DIR / "commands"
_OPENCODE_AGENTS_DIR = _OPENCODE_DATA_DIR / "agents"
_OPENCODE_SKILLS_DIR = _OPENCODE_DATA_DIR / "skills"
_OPENCODE_SKILLS_INVENTORY = _OPENCODE_DATA_DIR / "skills_inventory.yaml"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_trw_mcp_entry() -> OpencodeServerEntry:
    """Return the TRW MCP server entry for opencode.json.

    Uses local stdio transport (one trw-mcp process per instance).
    Falls back to absolute Python path if trw-mcp not on PATH.

    No ``--debug``: log verbosity is protocol, not per-client surface density,
    so every profile now generates the same command. Verbose logging is opted
    into portably via ``.trw/config.yaml`` ``debug: true``.
    """
    if shutil.which("trw-mcp"):
        command: list[str] = ["trw-mcp"]
    else:
        command = [sys.executable, "-m", "trw_mcp.server"]

    return {
        "type": "local",
        "command": command,
        "enabled": True,
    }


def _strip_jsonc_comments(content: str) -> str:
    """Strip ``//`` line and ``/* */`` block comments from a JSONC string.

    The string-aware core shared by :func:`_parse_jsonc` (which then parses the
    result) and :func:`_read_existing_opencode_config` (which parses through
    ``json.loads`` so the parsed value is genuinely untyped and its top-level
    shape can be validated). Comment delimiters inside JSON string literals are
    preserved.
    """
    # Remove block comments /* ... */ (including multi-line)
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    # Remove line comments // ... (but not inside strings)
    # Simple approach: remove // comments that are on their own segment
    # Uses a regex that skips strings
    result_parts: list[str] = []
    i = 0
    in_string = False
    escape_next = False
    while i < len(content):
        ch = content[i]
        if escape_next:
            result_parts.append(ch)
            escape_next = False
            i += 1
            continue
        if ch == "\\" and in_string:
            escape_next = True
            result_parts.append(ch)
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            result_parts.append(ch)
            i += 1
            continue
        if not in_string and ch == "/" and i + 1 < len(content) and content[i + 1] == "/":
            # Skip to end of line
            while i < len(content) and content[i] != "\n":
                i += 1
            continue
        result_parts.append(ch)
        i += 1
    return "".join(result_parts)


def _parse_jsonc(content: str) -> OpencodeConfig:
    """Parse JSONC (JSON with comments) by stripping comments.

    Handles // line comments and /* block comments */.
    Returns parsed dict. Raises json.JSONDecodeError on invalid JSON.
    """
    result: OpencodeConfig = json.loads(_strip_jsonc_comments(content))
    return result


def _read_existing_opencode_config(
    path: Path,
    *,
    result: dict[str, list[str]],
) -> OpencodeConfig | None:
    """Read an existing ``opencode.json`` as a JSONC object, or return ``None``.

    The deep seam behind the FR16 smart-merge read. It is JSONC-aware (the
    OpenCode config permits ``//`` and ``/* */`` comments, so it cannot reuse
    the plain-JSON :func:`read_json_object` seam) yet shares that seam's
    fail-closed, content-free policy: every malformed-input outcome collapses to
    ``None`` plus a structural diagnostic, never a crash.

    Outcomes (the prior call site read text + parsed inline and caught only
    ``json.JSONDecodeError`` / ``OSError``, so the first two below escaped or
    surfaced raw parser context):

      - **unreadable** — ``OSError`` (permission, race, is-a-directory, ...).
      - **non_utf8** — bytes are not valid UTF-8. ``bytes.decode("utf-8")``
        raises ``UnicodeDecodeError`` (a ``ValueError`` subclass, *not* an
        ``OSError``), which the prior call site let escape and crash bootstrap.
      - **malformed_json** — valid UTF-8 but the JSONC payload will not parse.
      - **non_object** — parses, but the top level is an array or scalar;
        :func:`merge_opencode_json` would then ``.get(...)`` on a non-mapping
        and raise ``AttributeError``.

    On every failure a *content-free* reason category
    (``unreadable`` / ``non_utf8`` / ``malformed_json`` / ``non_object``) is
    appended to ``result["errors"]`` against the stable rel-name
    ``opencode.json`` — never an absolute path, the raw bytes, a secret marker,
    the decode offset, or ``str(exc)``. A malformed config that happens to hold
    a token therefore never leaks into the result or logs.

    Returns the parsed mapping on success, else ``None`` (the caller reports the
    recorded error and leaves the user's file untouched).
    """
    rel = path.name  # stable "opencode.json"; never the absolute path

    try:
        raw = path.read_bytes()
    except OSError:
        logger.warning("opencode_json_unreadable", path=str(path), reason="unreadable")
        result["errors"].append(f"Failed to read {rel}: unreadable")
        return None

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning("opencode_json_non_utf8", path=str(path), reason="non_utf8")
        result["errors"].append(f"Failed to read {rel}: non_utf8")
        return None

    parsed: object
    try:
        parsed = json.loads(_strip_jsonc_comments(text))
    except json.JSONDecodeError:
        logger.warning("opencode_json_malformed", path=str(path), reason="malformed_json")
        result["errors"].append(f"Failed to read {rel}: malformed_json")
        return None

    if not isinstance(parsed, dict):
        logger.warning(
            "opencode_json_non_object",
            path=str(path),
            reason="non_object",
            json_kind=type(parsed).__name__,
        )
        result["errors"].append(f"Failed to read {rel}: non_object")
        return None

    return cast("OpencodeConfig", parsed)


# opencode's private ``_is_user_modified`` was deleted in favour of the shared
# ``_managed_client_artifacts.artifact_user_edited``. It consulted only the
# manifest, so it answered "not modified" — i.e. overwrite — whenever
# ``manifest_hashes`` was ``None`` or missing the key. A corrupt, absent, or
# pre-content-hash ``managed-artifacts.yaml`` therefore cost the user their edits
# silently: "we could not check" degrading to the reassuring answer. The shared
# guard falls back to the incoming bundled bytes as a framework baseline instead.


def _copy_file(
    src: Path,
    dest: Path,
    rel_path: str,
    result: dict[str, list[str]],
    *,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
) -> None:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result["errors"].append(f"Failed to create directory {dest.parent}: {exc}")
        return

    try:
        incoming = src.read_bytes()
    except OSError as exc:
        result["errors"].append(f"Failed to read {src}: {exc}")
        return

    from ._managed_client_artifacts import artifact_user_edited

    if not force and artifact_user_edited(dest, rel_path, incoming, manifest_hashes):
        result["preserved"].append(rel_path)
        return

    try:
        existed = dest.exists()
        if existed and not force and dest.read_bytes() == incoming:
            result["preserved"].append(rel_path)
            return
        shutil.copy2(src, dest)
        result["updated" if existed else "created"].append(rel_path)
    except OSError as exc:
        result["errors"].append(f"Failed to copy {src} -> {dest}: {exc}")


def _copy_markdown_dir(
    source_dir: Path,
    target_dir: Path,
    rel_root: str,
    *,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    result = _new_result()
    if not source_dir.is_dir():
        return result

    for src in sorted(source_dir.iterdir()):
        if src.suffix != ".md":
            continue
        rel_path = f"{rel_root}/{src.name}"
        _copy_file(src, target_dir / src.name, rel_path, result, force=force, manifest_hashes=manifest_hashes)
    return result


def load_opencode_skill_inventory(data_dir: Path | None = None) -> dict[str, dict[str, str]]:
    """Load the OpenCode skill compatibility inventory."""
    from ruamel.yaml import YAML

    inventory_path = (data_dir or _OPENCODE_DATA_DIR) / "skills_inventory.yaml"
    yaml = YAML(typ="safe")
    data = yaml.load(inventory_path.read_text(encoding="utf-8")) or {}
    raw_skills = data.get("skills", {})
    if not isinstance(raw_skills, dict):
        return {}
    skills: dict[str, dict[str, str]] = {}
    for name, config in raw_skills.items():
        if isinstance(name, str) and isinstance(config, dict):
            skills[name] = {str(k): str(v) for k, v in config.items()}
    return skills


def install_opencode_commands(
    target_dir: Path,
    *,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Install bundled OpenCode native commands into ``.opencode/commands``."""
    return _copy_markdown_dir(
        _OPENCODE_COMMANDS_DIR,
        target_dir / ".opencode" / "commands",
        ".opencode/commands",
        force=force,
        manifest_hashes=manifest_hashes,
    )


def install_opencode_agents(
    target_dir: Path,
    *,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Install bundled OpenCode specialist agents into ``.opencode/agents``."""
    return _copy_markdown_dir(
        _OPENCODE_AGENTS_DIR,
        target_dir / ".opencode" / "agents",
        ".opencode/agents",
        force=force,
        manifest_hashes=manifest_hashes,
    )


def install_opencode_skills(
    target_dir: Path,
    *,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
    data_dir: Path | None = None,
) -> dict[str, list[str]]:
    """Install the curated OpenCode skill subset into ``.opencode/skills``."""
    result = _new_result()
    base_dir = data_dir or _OPENCODE_DATA_DIR
    inventory = load_opencode_skill_inventory(base_dir)
    variant_root = base_dir / "skills"
    dest_root = target_dir / ".opencode" / "skills"

    for skill_name, cfg in sorted(inventory.items()):
        if cfg.get("disposition") == "exclude":
            continue
        skill_dir = variant_root / skill_name
        if not skill_dir.is_dir():
            result["errors"].append(f"Missing OpenCode skill variant for {skill_name}")
            continue
        try:
            dest_skill = dest_root / skill_name
            dest_skill.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            result["errors"].append(f"Failed to create directory {dest_root / skill_name}: {exc}")
            continue
        for skill_file in sorted(skill_dir.iterdir()):
            if not skill_file.is_file():
                continue
            rel_path = f".opencode/skills/{skill_name}/{skill_file.name}"
            _copy_file(
                skill_file,
                dest_root / skill_name / skill_file.name,
                rel_path,
                result,
                force=force,
                manifest_hashes=manifest_hashes,
            )
    return result


# ---------------------------------------------------------------------------
# Smart merge (FR16)
# ---------------------------------------------------------------------------


def merge_opencode_json(
    existing: OpencodeConfig,
    trw_entry: OpencodeServerEntry,
) -> OpencodeConfig:
    """Smart merge TRW config into existing opencode.json (FR16).

    Rules:
    - Add/update "trw" entry under "mcp" without removing other servers.
    - Add "permission" defaults only if "permission" key doesn't exist.
    - NEVER overwrite user's "model", "small_model", "agent".
    - APPEND the TRW instructions artifact to "instructions", preserving every
      user entry at its original index (PRD-CORE-240-FR05).
    - Preserve all other keys.
    """
    # Start from existing config via unpacking — preserves all user keys
    # (model, small_model, agent, instructions) without dynamic key access.
    result: OpencodeConfig = {**existing}

    # Update "mcp" section: add/update "trw" key, preserve others
    mcp: dict[str, OpencodeServerEntry] = dict(result.get("mcp", {}))
    mcp["trw"] = trw_entry
    result["mcp"] = mcp

    # Add default permissions only when the key is absent
    if "permission" not in result:
        result["permission"] = dict(_DEFAULT_PERMISSIONS)

    result["instructions"] = _merge_instructions(result.get("instructions"))

    return result


def _merge_instructions(existing: list[str] | None) -> list[str]:
    """Return the user's ``instructions`` array with the TRW artifact appended once.

    OpenCode has no in-file include syntax; ``opencode.json``'s ``instructions``
    array is how a file gets loaded. TRW writes ``.opencode/INSTRUCTIONS.md``
    unconditionally, but only the FRESH-INSTALL branch ever seeded the array —
    this merge path documented that it "never overwrites the user's instructions
    key" and, correctly, never did, but it never *added* to it either. So for any
    project that had an ``opencode.json`` before TRW was installed, the
    instructions file was written and referenced by nothing: a file loaded by
    nobody, with every surface reporting success (PRD-CORE-240-FR05).

    Appending is safe precisely because the array is multi-valued — unlike
    codex's single-valued ``model_instructions_file``, which is why that client
    is deliberately NOT repointed. User entries keep their original order and
    index; the TRW entry goes last; a re-run appends nothing.
    """
    entries = list(existing) if existing else []
    if _OPENCODE_INSTRUCTIONS_ENTRY not in entries:
        entries.append(_OPENCODE_INSTRUCTIONS_ENTRY)
    return entries


# ---------------------------------------------------------------------------
# opencode.json generation (FR11)
# ---------------------------------------------------------------------------


def generate_opencode_config(
    target_dir: Path,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Generate opencode.json with TRW MCP server config.

    If opencode.json already exists, performs smart merge (FR16).
    If not, writes full template.

    Returns dict with 'created', 'updated', 'preserved', 'errors' lists.
    """
    result = _new_result()
    config_path = target_dir / "opencode.json"
    trw_entry = _get_trw_mcp_entry()

    if config_path.exists() and not force:
        # Smart merge path (FR16). The read seam fails closed and content-free:
        # on unreadable/non-UTF-8/malformed/non-object input it returns None
        # after recording a structural reason, so the user's file is preserved.
        existing = _read_existing_opencode_config(config_path, result=result)
        if existing is None:
            return result

        merged = merge_opencode_json(existing, trw_entry)
        try:
            config_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
            result["updated"].append(config_path.name)
        except OSError as exc:
            result["errors"].append(f"Failed to write {config_path.name}: {exc}")
    else:
        # Fresh install: write full template with .opencode/INSTRUCTIONS.md
        template: OpencodeTemplateDict = {
            "$schema": "https://opencode.ai/config.json",
            "instructions": [_OPENCODE_INSTRUCTIONS_ENTRY],
            "permission": dict(_DEFAULT_PERMISSIONS),
            "tools": {"trw*": True},
            "mcp": {"trw": trw_entry},
        }
        try:
            config_path.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
            result["created"].append(config_path.name)
        except OSError as exc:
            result["errors"].append(f"Failed to write {config_path.name}: {exc}")

    logger.debug(
        "generate_opencode_config",
        created=result["created"],
        updated=result["updated"],
    )
    return result


# ---------------------------------------------------------------------------
# AGENTS.md generation (FR11)
# ---------------------------------------------------------------------------


def _writes_shared_agents_md(client_id: str) -> bool:
    """Return whether *client_id*'s profile declares the shared AGENTS.md.

    Derived from the profile registry rather than a literal list here, so a
    client whose tier changes is respected automatically. An unresolvable client
    is treated as writing (the pre-existing behaviour) rather than silently
    dropping a surface.
    """
    from trw_mcp.models.config._profiles import resolve_client_profile

    try:
        return bool(resolve_client_profile(client_id).write_targets.agents_md)
    except Exception:  # justified: an unknown client keeps the prior write behaviour
        return True


def generate_agents_md(
    target_dir: Path,
    trw_section: str,
    *,
    force: bool = False,
    client_id: str = "",
) -> dict[str, list[str]]:
    """Generate or update AGENTS.md with TRW auto-generated section.

    Writes nothing when *client_id*'s profile declares
    ``write_targets.agents_md=False`` (PRD-CORE-240-FR04). opencode and codex
    each own a whole TRW-authored instruction file wired through their own
    config, so writing the shared AGENTS.md on top was injection into a
    user-owned file that no client reads. The gate lives HERE rather than at the
    four call sites so a fifth caller cannot reintroduce the write by omission;
    the profile is the authority, not a literal in this module.

    Uses same <!-- trw:start --> / <!-- trw:end --> markers as CLAUDE.md.
    If file exists, replaces only the section between markers.
    If not, creates new file with the section.

    Acquires the shared ``.trw/channels/agents-md.lock`` (OC-B1 / PRD-DIST-2403 FR05)
    so that this ceremony writer and the distill segment writer cannot race.
    Fail-open: if the lock cannot be acquired, returns a skipped indication.
    """
    result = _new_result()

    if client_id and not _writes_shared_agents_md(client_id):
        logger.debug("generate_agents_md_profile_declines", client=client_id)
        return result

    # Acquire shared AGENTS.md lock (OC-B1 / PRD-DIST-2403 FR05)
    try:
        lock = agents_md_lock(target_dir)
        lock.__enter__()
    except ChannelLockSkip:
        logger.debug("generate_agents_md_lock_skip", outcome="skipped_lock")
        result["errors"].append("AGENTS.md write skipped: could not acquire agents-md lock")
        return result

    try:
        agents_md_path = target_dir / "AGENTS.md"

        new_block = f"{_TRW_HEADER}\n{_TRW_START_MARKER}\n{trw_section}\n{_TRW_END_MARKER}\n"

        if agents_md_path.exists() and not force:
            content = agents_md_path.read_text(encoding="utf-8")
            # Shared line-anchored replacer — never a raw substring scan.
            markers = ((_TRW_START_MARKER, "start"), (_TRW_END_MARKER, "end"))
            updated = replace_marker_region(
                content, start=_TRW_START_MARKER, end=_TRW_END_MARKER, new_block=new_block, header=_TRW_HEADER
            )

            if updated is not None:
                try:
                    agents_md_path.write_text(updated, encoding="utf-8")
                    result["updated"].append(str(agents_md_path.name))
                except OSError as exc:
                    result["errors"].append(f"Failed to update {agents_md_path}: {exc}")
            elif not has_marker(content, *markers):  # no section at all -> append
                # No TRW section yet — append it
                if not content.endswith("\n"):
                    content += "\n"
                content += "\n" + new_block
                try:
                    agents_md_path.write_text(content, encoding="utf-8")
                    result["updated"].append(str(agents_md_path.name))
                except OSError as exc:
                    result["errors"].append(f"Failed to update {agents_md_path}: {exc}")
            else:
                result["errors"].append("AGENTS.md has malformed TRW markers — found start but not end")
        else:
            # Create new file
            try:
                agents_md_path.write_text(new_block, encoding="utf-8")
                result["created"].append(str(agents_md_path.name))
            except OSError as exc:
                result["errors"].append(f"Failed to write {agents_md_path}: {exc}")
    finally:
        try:
            lock.__exit__(None, None, None)
        except Exception:  # justified: fail-open, lock cleanup must not mask write results
            logger.debug("opencode_agents_md_lock_release_failed", exc_info=True)

    logger.debug(
        "generate_agents_md",
        created=result["created"],
        updated=result["updated"],
    )
    return result


# ---------------------------------------------------------------------------
# Model family detection
# ---------------------------------------------------------------------------
